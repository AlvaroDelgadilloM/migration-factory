"""Worker behaviour: process-group cancel/timeout, leases and recovery, dry-run, apply with per-step
rollback, diff review, gates and draft PR. Maven is faked (see helpers.FakeMaven)."""
import os
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from conftest import TMP
from mf.worker.source import content_hash
from helpers import FakeMaven, install, run_job, scanned_project
from mf.db import session
from mf.models import Job, JobEvent, Outbox, now
from mf.worker import main as worker_main, providers, runner, tasks

API = '/api/v1'


def ctx(tmp_path, seconds=60):
    return runner.Context('test', tmp_path, deadline=time.monotonic() + seconds, emit=lambda *a: None)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_runner_timeout_kills_whole_process_group(tmp_path):
    pidfile = tmp_path / 'child.pid'
    c = ctx(tmp_path)
    started = time.monotonic()
    with pytest.raises(runner.TimedOut):
        runner.run(['sh', '-c', f'sleep 300 & echo $! > {pidfile}; wait'], tmp_path, c, name='t', timeout=2)
    assert time.monotonic() - started < 20
    time.sleep(0.5)
    assert not alive(int(pidfile.read_text()))  # the grandchild died with the group


def test_runner_cancel_and_clean_env(tmp_path, monkeypatch):
    monkeypatch.setenv('MF_DATABASE_URL', 'postgresql://secret')
    c = ctx(tmp_path)
    res = runner.run(['sh', '-c', 'env'], tmp_path, c, name='env', timeout=10)
    env_out = res.log_path.read_text()
    assert 'MF_DATABASE_URL' not in env_out and 'secret' not in env_out
    pidfile = tmp_path / 'c.pid'
    threading.Timer(1.0, c.cancel.set).start()
    with pytest.raises(runner.Cancelled):
        runner.run(['sh', '-c', f'sleep 300 & echo $! > {pidfile}; wait'], tmp_path, c, name='c', timeout=60)
    time.sleep(0.5)
    assert not alive(int(pidfile.read_text()))
    with pytest.raises(TypeError):
        runner.run('echo hi', tmp_path, ctx(tmp_path), name='x', timeout=5)  # never a shell string


def test_claim_is_exclusive_and_recovery_requeues(client, token, pilot_repo, monkeypatch):
    from helpers import new_project
    install(monkeypatch, FakeMaven())
    pid = new_project(client, token, pilot_repo)
    job_id = client.post(f'{API}/projects/{pid}/scans', headers=token('diego.dev'), json={}).json()['jobId']
    assert tasks.claim(job_id) is not None
    assert tasks.claim(job_id) is None  # second delivery while leased: ignored
    with session() as s:
        s.get(Job, uuid.UUID(job_id)).lease_until = now() - timedelta(seconds=1)  # worker died
        s.commit()
    worker_main.recover_leases()
    with session() as s:
        job = s.get(Job, uuid.UUID(job_id))
        assert job.state == 'queued' and job.attempts == 1
        assert s.query(Outbox).filter(Outbox.payload['jobId'].as_string() == job_id).count() == 2
    run_job(job_id)  # second attempt completes from a fresh workspace
    with session() as s:
        job = s.get(Job, uuid.UUID(job_id))
        assert job.state == 'succeeded' and job.attempts == 2
        job.state, job.lease_until, job.attempts = 'running', now() - timedelta(seconds=1), job.max_attempts
        s.commit()
    worker_main.recover_leases()
    with session() as s:
        assert s.get(Job, uuid.UUID(job_id)).error_code == 'LEASE_EXPIRED'


def test_running_job_cancel_and_timeout(client, token, pilot_repo, monkeypatch):
    from helpers import new_project
    install(monkeypatch, FakeMaven(slow=120))
    pid = new_project(client, token, pilot_repo)
    r = client.post(f'{API}/projects/{pid}/scans', headers=token('diego.dev'), json={}).json()
    t = threading.Thread(target=run_job, args=(r['jobId'],))
    t.start()
    for _ in range(100):  # wait until the slow fake maven runs
        if client.get(f"{API}/jobs/{r['jobId']}", headers=token('diego.dev')).json()['phase'] == 'maven-effective':
            break
        time.sleep(0.2)
    resp = client.post(f"{API}/jobs/{r['jobId']}/cancel", headers=token('diego.dev')).json()
    assert resp['state'] == 'running' and resp['cancelRequested']  # not cancelled until the worker confirms
    t.join(60)
    assert not t.is_alive()
    assert client.get(f"{API}/jobs/{r['jobId']}", headers=token('diego.dev')).json()['state'] == 'cancelled'
    assert client.get(f"{API}/scans/{r['scanId']}", headers=token('diego.dev')).json()['status'] == 'cancelled'
    assert not (TMP / 'work' / r['jobId']).exists()  # workspace removed

    from mf.config import get_settings
    monkeypatch.setattr(get_settings(), 'job_timeout_seconds', 3)
    r2 = client.post(f'{API}/projects/{pid}/scans', headers=token('diego.dev'), json={}).json()
    run_job(r2['jobId'])
    job = client.get(f"{API}/jobs/{r2['jobId']}", headers=token('diego.dev')).json()
    assert job['state'] == 'timed_out' and job['errorCode'] == 'TIMEOUT'


def _ready_plan(client, token, scan, approve=True):
    prof = next(p for p in client.get(f'{API}/profiles', headers=token('ana.arquitecta')).json() if p['runtime'] == 'spring' and p['version'] == 3)
    plan = client.post(f"{API}/scans/{scan['id']}/plans", headers=token('ana.arquitecta'), json={'profileId': prof['id']}).json()
    f = client.get(f"{API}/scans/{scan['id']}/findings", headers=token('ana.arquitecta'), params={'rule': 'CAMEL2_VERSION'}).json()['items'][0]
    client.patch(f"{API}/findings/{f['id']}/review", headers=token('ana.arquitecta') | {'If-Match': str(f['version'])},
                 json={'decision': 'resolved', 'reason': 'Revisión manual 2→3 completada para el piloto'})
    if approve:
        client.post(f"{API}/plans/{plan['id']}/approve", headers=token('ana.arquitecta') | {'If-Match': str(plan['rowVersion'])})
    return plan


def test_preview_dry_run_produces_diff_without_touching_source(client, token, pilot_repo, monkeypatch):
    head_before = content_hash(Path(pilot_repo))
    pid, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    plan = _ready_plan(client, token, scan, approve=False)
    r = client.post(f"{API}/plans/{plan['id']}/preview", headers=token('diego.dev') | {'Idempotency-Key': 'pv-1'})
    assert r.status_code == 202
    assert client.post(f"{API}/plans/{plan['id']}/preview", headers=token('diego.dev') | {'Idempotency-Key': 'pv-1'}).json() == r.json()
    run_job(r.json()['jobId'])
    ex = client.get(f"{API}/executions/{r.json()['executionId']}", headers=token('diego.dev')).json()
    assert ex['state'] == 'succeeded' and ex['mode'] == 'preview'
    assert {s['state'] for s in ex['steps']} == {'previewed'}
    files = client.get(f"{API}/executions/{ex['id']}/diff", headers=token('diego.dev')).json()['items']
    assert files[0]['path'] == 'pom.xml' and files[0]['recipes'] == ['org.apache.camel.upgrade.CamelMigrationRecipe']
    assert content_hash(Path(pilot_repo)) == head_before


def test_apply_commits_per_step_rolls_back_failed_step_and_validates(client, token, pilot_repo, monkeypatch):
    head_before = content_hash(Path(pilot_repo))
    pid, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    plan = _ready_plan(client, token, scan)
    fake = install(monkeypatch, FakeMaven(fail_steps={'jakarta-jms'}))
    r = client.post(f"{API}/plans/{plan['id']}/executions", headers=token('diego.dev'), json={'mode': 'apply'}).json()
    assert client.post(f"{API}/plans/{plan['id']}/executions", headers=token('diego.dev'), json={'mode': 'apply'}).json()['code'] == 'EXECUTION_IN_PROGRESS'
    run_job(r['jobId'])
    ex = client.get(f"{API}/executions/{r['executionId']}", headers=token('diego.dev')).json()
    assert ex['state'] == 'failed', ex['job']  # a mandatory recipe failed: never 'succeeded' (doc 16)
    assert ex['summary']['primaryReason'] == 'MANDATORY_RECIPE_FAILED' and ex['job']['errorCode'] == 'MANDATORY_RECIPE_FAILED'
    steps = {s['key']: s for s in ex['steps']}
    assert steps['camel-3-to-4']['state'] == 'applied' and len(steps['camel-3-to-4']['commit']) == 40
    assert steps['jakarta-jms']['state'] == 'failed-rolled-back'
    assert steps['runtime-spring']['state'] == 'applied'
    files = {f['path']: f for f in client.get(f"{API}/executions/{ex['id']}/diff", headers=token('diego.dev')).json()['items']}
    assert 'pom.xml' in files and files['pom.xml']['recipes'] == ['camel-3-to-4']
    assert not any('JmsConfig' in p for p in files)  # rolled back change is not in the candidate
    detail = client.get(f"{API}/diff-files/{files['pom.xml']['id']}", headers=token('diego.dev')).json()
    assert any(l.startswith('+') and '<camel.version>4.14.0</camel.version>' in l for l in detail['patch'].splitlines())
    assert 'garbage' not in detail['patch']
    vals = {(v['suite'], v['target']): v for v in client.get(f"{API}/executions/{ex['id']}/validations", headers=token('diego.dev')).json()}
    assert vals[('compile', 'baseline')]['status'] == 'PASS' and vals[('test', 'candidate')]['tests']['tests'] == 3
    assert vals[('secret-scan', 'candidate')]['status'] == 'PASS'
    log_art = vals[('compile', 'candidate')]['artifactId']
    log = client.get(f'{API}/artifacts/{log_art}/download', headers=token('diego.dev')).text
    assert 'supersecret' not in log and 'password=***' in log  # logs are redacted
    gates = {g['gate']: g['status'] for g in ex['gates']}
    assert gates['G0'] == 'PASS' and gates['G2'] == 'PASS' and gates['G3'] == 'PASS' and gates['G4'] == 'NOT_RUN' and gates['G6'] == 'NOT_RUN'
    assert any('G4' in b for b in ex['prBlockers']) and any('bloqueantes' in b for b in ex['prBlockers'])
    assert content_hash(Path(pilot_repo)) == head_before  # the source repository was never modified
    names = {a['name'] for a in client.get(f'{API}/projects/{pid}/artifacts', headers=token('aldo.auditor'), params={'executionId': ex['id']}).json()['items']}
    assert {'candidate.patch', 'candidate.zip', 'execution-report.html', 'execution-report.json'} <= names

    # independent review: the executor cannot approve its own diff; reviewer can
    fid, ver = files['pom.xml']['id'], files['pom.xml']['version']
    ana_ex = client.post(f"{API}/diff-files/{fid}/review", headers=token('diego.dev') | {'If-Match': str(ver)}, json={'decision': 'approved'})
    assert ana_ex.status_code == 403
    assert client.post(f"{API}/diff-files/{fid}/review", headers=token('rosa.revisora') | {'If-Match': str(ver)},
                       json={'decision': 'rejected', 'reason': 'x'}).status_code == 422
    for f in files.values():
        assert client.post(f"{API}/diff-files/{f['id']}/review", headers=token('rosa.revisora') | {'If-Match': str(f['version'])},
                           json={'decision': 'approved'}).status_code == 200
    ev = client.post(f"{API}/executions/{ex['id']}/validations", headers=token('rosa.revisora'),
                     json={'suite': 'equivalence', 'status': 'PASS', 'detail': 'Suite de contratos y JMS del proyecto: ver evidencia EV-12'})
    assert ev.status_code == 201
    ex = client.get(f"{API}/executions/{ex['id']}", headers=token('diego.dev')).json()
    gates = {g['gate']: g['status'] for g in ex['gates']}
    assert gates['G4'] == 'PASS' and gates['G6'] == 'PASS'
    # still blocked: open MANUAL finding (vm:) and no Git configuration
    r = client.post(f"{API}/executions/{ex['id']}/pull-request", headers=token('rosa.revisora'))
    assert r.status_code == 409 and r.json()['code'] == 'GATES_NOT_MET'
    # rollback discards the candidate
    rb = client.post(f"{API}/executions/{ex['id']}/rollback", headers=token('diego.dev')).json()
    assert rb['rolledBack'] and rb['state'] == 'rolled_back'
    assert client.post(f"{API}/executions/{ex['id']}/rollback", headers=token('diego.dev')).status_code == 409


def test_sse_events_resume_from_last_event_id(client, token, pilot_repo, monkeypatch):
    pid, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    job = scan['job']['id']
    logs = client.get(f'{API}/jobs/{job}/logs', headers=token('aldo.auditor')).json()
    assert isinstance(logs, list), logs
    assert len(logs) > 3
    with client.stream('GET', f'{API}/jobs/{job}/events', headers=token('aldo.auditor') | {'Last-Event-ID': str(logs[-2]['id'])}) as r:
        body = ''.join(r.iter_text())
    assert body.count('event: log') == 1 and 'event: end' in body
    assert client.get(f'{API}/jobs/{job}/events', headers=token('mallory.externa')).status_code == 404


def test_github_adapter_creates_draft_pr():
    seen = {}

    def handler(request):
        seen['auth'], seen['json'] = request.headers['authorization'], request.read()
        return httpx.Response(201, json={'html_url': 'https://github.com/acme/orders/pull/7'})
    url = providers.create_draft_pr({'provider': 'github', 'repository': 'acme/orders', 'baseBranch': 'main'}, 'mf/abc',
                                    title='t', body='b', token='tok123', transport=httpx.MockTransport(handler))
    assert url.endswith('/pull/7') and seen['auth'] == 'Bearer tok123' and b'"draft":true' in seen['json'].replace(b' ', b'')
    with pytest.raises(providers.ProviderError):
        providers.create_draft_pr({'provider': 'github', 'repository': 'a/b'}, 'mf/x', title='t', body='b', token='t',
                                  transport=httpx.MockTransport(lambda r: httpx.Response(422)))


def test_worker_scrubs_secrets_from_environment(monkeypatch):
    monkeypatch.setenv('MF_CRED_GIT', 'tok-value')
    for k in ('MF_DEV_JWT_SECRET', 'MF_DATABASE_URL', 'MF_REDIS_URL'):
        monkeypatch.setenv(k, os.environ[k])  # restored after the test
    worker_main.harden_process()
    assert 'MF_CRED_GIT' not in os.environ and tasks.CREDENTIALS['MF_CRED_GIT'] == 'tok-value'
    assert 'MF_DEV_JWT_SECRET' not in os.environ


def test_invalid_maven_model_blocks_recipes(client, token, pilot_repo, monkeypatch):
    pid, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    plan = _ready_plan(client, token, scan)
    install(monkeypatch, FakeMaven(model_ok=False))
    r = client.post(f"{API}/plans/{plan['id']}/executions", headers=token('diego.dev'), json={'mode': 'apply'}).json()
    run_job(r['jobId'])
    ex = client.get(f"{API}/executions/{r['executionId']}", headers=token('diego.dev')).json()
    assert ex['state'] == 'blocked' and ex['job']['errorCode'] == 'CANDIDATE_MAVEN_MODEL_INVALID'
    assert {s['state'] for s in ex['steps'] if s['kind'] == 'recipe'} == {'blocked-by-baseline'}
    vals = {v['suite']: v['status'] for v in client.get(f"{API}/executions/{r['executionId']}/validations", headers=token('diego.dev')).json()}
    assert vals['maven-model'] == 'FAIL' and 'compile' not in vals  # nothing ran after the failed gate


def test_aggregate_status_rules():
    """Doc 16 §9-§12: SUCCEEDED only when every gate passes; SKIPPED never counts as success."""
    agg = tasks.aggregate_status
    ok = {('maven-model', 'candidate'): 'PASS', ('compile', 'candidate'): 'PASS', ('test', 'candidate'): 'PASS', ('secret-scan', 'candidate'): 'PASS'}
    steps = [{'key': 'camel-3-to-4', 'kind': 'recipe', 'state': 'applied'}, {'key': 'endpoints-config', 'kind': 'transform', 'state': 'applied'}]
    assert agg(steps, ok)['status'] == 'SUCCEEDED'
    r = agg(steps, ok | {('compile', 'candidate'): 'FAIL', ('test', 'candidate'): 'SKIPPED'})
    assert r['status'] == 'FAILED' and r['primaryReason'] == 'CANDIDATE_COMPILE_FAILED' and 'SKIPPED_DUE_TO_COMPILE_FAILURE' in r['reasonCodes']
    r = agg([{'key': 'jakarta-jms', 'kind': 'recipe', 'state': 'failed-rolled-back'}] + steps, ok)
    assert r['status'] == 'FAILED' and r['primaryReason'] == 'MANDATORY_RECIPE_FAILED'  # rollback kept, status still FAILED
    assert agg([{'key': 'runtime-spring', 'kind': 'recipe', 'state': 'skipped'}], ok)['primaryReason'] == 'MANDATORY_RECIPE_NOT_APPLIED'
    r = agg(steps, ok | {('secret-scan', 'candidate'): 'FINDINGS'})
    assert r['status'] == 'PARTIAL_SUCCESS' and r['reasonCodes'] == ['SECRET_FINDINGS']
    assert agg(steps, ok | {('secret-scan', 'candidate'): 'ERROR'})['reasonCodes'] == ['SECRET_SCANNER_ERROR']  # never confused
    assert agg(steps, ok | {('maven-model', 'candidate'): 'FAIL'})['primaryReason'] == 'CANDIDATE_MAVEN_MODEL_INVALID'
    assert agg(steps, ok, plan_blocked=[{'key': 'manual-camel-vm'}])['status'] == 'PARTIAL_SUCCESS'
    assert agg(steps, ok, manual_actions=3)['reasonCodes'] == ['MANUAL_ACTIONS_PENDING']


def test_recipe_that_breaks_the_pom_is_rolled_back(client, token, pilot_repo, monkeypatch):
    pid, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    plan = _ready_plan(client, token, scan)
    fake = FakeMaven(model_ok=lambda cwd: 'BROKEN' not in (Path(cwd) / 'pom.xml').read_text())
    install(monkeypatch, fake)
    orig = fake.rewrite

    def rewrite(ctx, repo, goal, **kw):
        res = orig(ctx, repo, goal, **kw)
        if kw['name'] == 'rewrite-camel-3-to-4':  # simulate Camel 4 versions with Camel-2-only artifacts
            pom = Path(repo) / 'pom.xml'
            pom.write_text(pom.read_text() + '<!-- BROKEN -->')
        return res
    monkeypatch.setattr('mf.worker.mavenops.rewrite', rewrite)
    r = client.post(f"{API}/plans/{plan['id']}/executions", headers=token('diego.dev'), json={'mode': 'apply'}).json()
    run_job(r['jobId'])
    ex = client.get(f"{API}/executions/{r['executionId']}", headers=token('diego.dev')).json()
    steps = {s['key']: s for s in ex['steps']}
    assert steps['camel-3-to-4']['state'] == 'failed-rolled-back' and 'CANDIDATE_MAVEN_MODEL_INVALID' in steps['camel-3-to-4']['reasons'][0]
    assert steps['jakarta-jms']['state'] == 'skipped' and ex['state'] == 'failed'


def test_change_ledger_and_modernization_on_the_platform(client, token, pilot_repo, monkeypatch):
    """improvements/14 (ledger per step) and 12-PROMPT-CLAUDE-MODERNIZATION exposed through the API + worker."""
    pid, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    plan = _ready_plan(client, token, scan)
    install(monkeypatch, FakeMaven())
    r = client.post(f"{API}/plans/{plan['id']}/executions", headers=token('diego.dev'), json={'mode': 'apply'}).json()
    run_job(r['jobId'])
    ex = client.get(f"{API}/executions/{r['executionId']}", headers=token('diego.dev')).json()
    assert ex['state'] in ('succeeded', 'partial_success'), ex['summary']
    changes = client.get(f"{API}/executions/{ex['id']}/changes", headers=token('aldo.auditor')).json()
    camel = next(c for c in changes if c['step'] == 'camel-3-to-4')
    assert camel['confidence'] == 'AUTO_TEST' and camel['snapshotAfter'] == next(s for s in ex['steps'] if s['key'] == 'camel-3-to-4')['commit']
    assert any('4.14.0' in c['after'] for c in camel['changes']) and ex['summary']['changesRecorded'] == len(changes)
    patch = client.get(f"{API}/artifacts/{camel['patchArtifactId']}/download", headers=token('aldo.auditor')).text
    assert patch.startswith('diff --git') and camel['rollback']['command'].startswith('git apply -R step-')

    rules = {x['id']: x for x in client.get(f'{API}/modernization-rules', headers=token('aldo.auditor')).json()}
    assert rules['MOD_OUTBOX']['tier'] == 'ARCHITECTURE' and rules['MOD_FIELD_INJECTION']['publicContract']
    url = f"{API}/executions/{ex['id']}/modernizations"
    assert client.post(url, headers=token('aldo.auditor'), json={}).status_code == 403
    assert client.post(url, headers=token('ana.arquitecta'), json={'approve': ['MOD_OUTBOX']}).status_code == 422  # architecture: never
    assert client.post(url, headers=token('diego.dev'), json={'approve': ['MOD_FIELD_INJECTION']}).status_code == 403  # contract change: architect
    zip_before = next(a for a in client.get(f'{API}/projects/{pid}/artifacts', headers=token('diego.dev'),
                                            params={'executionId': ex['id']}).json()['items'] if a['name'] == 'candidate.zip')['sha256']
    acc = client.post(url, headers=token('diego.dev'), json={})
    assert acc.status_code == 202
    assert client.post(url, headers=token('diego.dev'), json={}).status_code == 409  # one at a time
    run_job(acc.json()['jobId'])
    mo = client.get(f"{API}/modernizations/{acc.json()['modernizationId']}", headers=token('aldo.auditor')).json()
    assert mo['state'] in ('succeeded', 'partial_success'), mo['job']
    assert mo['summary']['baseline'] == 'PASS' and mo['qualityBefore']['tests']['tests'] == 3
    assert {r['status'] for r in mo['results']} <= {'APPLIED', 'ROLLED_BACK', 'PROPOSED_ONLY', 'BLOCKED', 'NO_CHANGES'}
    assert any(r['rule'] == 'MOD_SAFE_CLEANUP' for r in mo['results'])
    assert {'modernization-report.md', 'refactors-applied.json', 'quality-before.json', 'quality-after.json', 'manual-recommendations.md',
            'modernized.zip'} <= set(mo['summary']['artifacts'])
    arts = client.get(f'{API}/projects/{pid}/artifacts', headers=token('diego.dev'), params={'executionId': ex['id']}).json()['items']
    assert next(a for a in arts if a['name'] == 'candidate.zip')['sha256'] == zip_before  # the execution candidate is unchanged
    assert [m['id'] for m in client.get(url, headers=token('aldo.auditor')).json()] == [mo['id']]


def test_local_result_is_a_download_not_a_pull_request(client, token, pilot_repo, monkeypatch):
    """19-CORRECCION part B: LOCAL -> download migrated-project.zip with .migration/ (no PR); label follows the real status."""
    import io
    import json
    import zipfile
    pid, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    plan = _ready_plan(client, token, scan)
    install(monkeypatch, FakeMaven())
    r = client.post(f"{API}/plans/{plan['id']}/executions", headers=token('diego.dev'), json={'mode': 'apply'}).json()
    run_job(r['jobId'])
    res = client.get(f"{API}/executions/{r['executionId']}/result", headers=token('aldo.auditor')).json()
    assert res['sourceType'] == 'LOCAL' and res['pullRequestAvailable'] is False and res['pullRequest']['shown'] is False
    assert [i['id'] for i in res['integrations']] == ['zip-download']
    assert res['status'] == 'PARTIAL_SUCCESS' and res['download']['label'] == 'Descargar proyecto migrado con pendientes' and res['download']['warning']
    assert res['artifacts']['diff'] and res['artifacts']['manualActions'] and res['artifacts']['report']
    d = client.get(f"{API}/executions/{r['executionId']}/download", headers=token('aldo.auditor'))
    assert d.status_code == 200 and 'filename="migrated-project.zip"' in d.headers['content-disposition']
    z = zipfile.ZipFile(io.BytesIO(d.content))
    names = z.namelist()
    man = json.loads(z.read('.migration/manifest.json'))
    assert man['status'] == 'PARTIAL_SUCCESS' and man['sourceType'] == 'LOCAL' and man['buildPassed'] and man['manualActions'] >= 1
    assert {'.migration/diff.patch', '.migration/manual-actions.md', '.migration/migration-report.md'} <= set(names)
    assert not any(n.startswith(('target/', '.git/')) or '/target/' in n for n in names)
    assert 'orders-routes/pom.xml' in names and '4.14.0' in z.read('pom.xml').decode()  # the final candidate, not the source
    # same execution seen as a remote Git source: PR is offered, gated with explicit reasons (never inferred from .git)
    from mf.db import session
    from mf.models import Project, Repository
    with session() as s:
        repo = s.get(Repository, s.get(Project, uuid.UUID(pid)).repository_id)
        repo.source_type, repo.provider = 'git', 'github'
        s.commit()
    res = client.get(f"{API}/executions/{r['executionId']}/result", headers=token('aldo.auditor')).json()
    assert res['sourceType'] == 'GIT_REMOTE' and res['pullRequest']['shown'] and not res['pullRequestAvailable']
    assert any('credencial' in x for x in res['pullRequest']['reasons']) and [i['id'] for i in res['integrations']] == ['zip-download', 'github-pr']


def test_long_jobs_use_their_own_queue():
    """Workspace migrations and modernizations never delay scans/executions (separate RQ queue and worker)."""
    from mf import jobs
    assert jobs.queue_for('workspace_migrate') == jobs.queue_for('modernize') == 'mf-long'
    assert {jobs.queue_for(k) for k in ('scan', 'preview', 'apply', 'pull_request')} == {'mf-jobs'}

"""17-CORRECCION-MULTI-PROJECT-ROOT-AMBIGUOUS: several POMs without an aggregator are a workspace, never ROOT_AMBIGUOUS.
Maven/OpenRewrite are faked; acceptance cases 1-5 plus the waves migration and the platform scan."""
import json
import shutil

import pytest

from conftest import ROOT
from helpers import FakeMaven, install, new_project, run_job
from mf import cli_tool, workspace
from mf.worker.source import content_hash

PILOT = ROOT / 'examples' / 'pilot-orders'
ACCEPT = 'CAMEL2_VERSION=Revisado contra la guía 2→3 en la fixture'
DEP = '</dependencies>'
# project -> internal dependencies (artifactId of the other project's module)
LAYOUT = {'common-datasource': [], 'common-activemq': [], 'common-rate-exchange': [],
          'common-catalogue': ['common-activemq'], 'common-epay': ['common-datasource', 'common-catalogue']}


def make_workspace(base, layout=LAYOUT):
    ws = base / 'develop' / 'commons'
    for name, deps in layout.items():
        p = ws / name
        shutil.copytree(PILOT, p)
        for pom in (p / 'pom.xml', p / 'orders-routes' / 'pom.xml'):
            t = pom.read_text().replace('demo.pilot', 'com.company.commons').replace('orders-parent', f'{name}-parent')
            t = t.replace('<artifactId>orders-routes</artifactId>', f'<artifactId>{name}</artifactId>')
            t = t.replace('<packaging>war</packaging>', '<packaging>jar</packaging>')  # shared commons are libraries (jar)
            if pom.parent.name == 'orders-routes' and deps:
                t = t.replace(DEP, ''.join(f'<dependency><groupId>com.company.commons</groupId><artifactId>{d}</artifactId>'
                                           f'<version>1.0.0</version></dependency>' for d in deps) + DEP, 1)
            pom.write_text(t)
    return base


def test_acceptance_cases_classification_graph_order(tmp_path):
    single = tmp_path / 'single'
    shutil.copytree(PILOT / 'orders-routes', single / 'project')
    assert workspace.classify_dir(single)['repositoryType'] == 'SINGLE_PROJECT'                                   # case 1
    assert workspace.classify_dir(PILOT)['repositoryType'] == 'MAVEN_MULTI_MODULE'                               # case 2
    base = make_workspace(tmp_path / 'ws')
    d = cli_tool.discover(base)                                                                                  # case 3
    assert d['info']['repositoryType'] == 'MULTI_PROJECT_REPOSITORY' and d['info']['workspaceRoot'] == 'develop/commons'
    assert [p['name'] for p in d['projects']] == sorted(LAYOUT) and d['info']['findings'][0]['type'] == 'ROOT_AMBIGUOUS_RESOLVED_AS_WORKSPACE'
    edges = {(e['from'], e['to']) for e in d['graph']['edges']}                                                  # case 4
    assert edges == {('common-catalogue', 'common-activemq'), ('common-epay', 'common-datasource'), ('common-epay', 'common-catalogue')}
    assert [w['projects'] for w in d['order']['waves']] == [['common-activemq', 'common-datasource', 'common-rate-exchange'],
                                                            ['common-catalogue'], ['common-epay']]
    assert not (base / 'develop' / 'commons' / 'pom.xml').exists()  # never an artificial parent POM


def test_cycle_blocks_only_the_affected_projects(tmp_path):                                                       # case 5
    base = make_workspace(tmp_path, {'a': ['b'], 'b': ['a'], 'c': []})
    o = cli_tool.discover(base)['order']
    assert o['cycles'][0]['projects'] == ['a', 'b'] and o['blocked'] == {'a': 'DEPENDENCY_CYCLE', 'b': 'DEPENDENCY_CYCLE'} and o['order'] == ['c']


def test_cli_analyze_workspace_does_not_abort_and_writes_outputs(tmp_path, capsys):
    base = make_workspace(tmp_path / 'src')
    out = tmp_path / 'workspace-analysis'
    assert cli_tool.main(['analyze', str(base), '--output', str(out), '--no-maven']) == 0  # automatic: was ROOT_AMBIGUOUS
    res = json.loads(capsys.readouterr().out)
    assert res['repositoryType'] == 'MULTI_PROJECT_REPOSITORY' and len(res['projects']) == 5 and res['shared']['Camel 2'] == '5/5'
    wsd = out / 'workspace'                                                                           # doc 20 §16 layout
    assert {'workspace-analysis.md', 'dependency-graph.json', 'migration-order.json', 'shared-findings.json', 'analysis-waves.json'} <= {p.name for p in wsd.iterdir()}
    assert sorted(p.name for p in (out / 'projects').iterdir()) == sorted(LAYOUT)                     # one report per project
    assert all((out / 'projects' / n / 'analysis' / 'analysis.json').exists() and (out / 'projects' / n / 'analysis' / 'report.md').exists() for n in LAYOUT)
    assert res['projectsDiscovered'] == 5 and res['projectsAnalyzed'] == 5 and res['analysisStatus'] == 'ANALYSIS_SUCCEEDED'  # A1
    waves = json.loads((wsd / 'analysis-waves.json').read_text())                                     # A4
    assert [w['projects'] for w in waves] == [['common-activemq', 'common-datasource', 'common-rate-exchange'], ['common-catalogue'], ['common-epay']]
    assert all(w['status'] == 'ANALYSIS_WAVE_SUCCEEDED' for w in waves)
    epay = json.loads((out / 'projects' / 'common-epay' / 'analysis' / 'analysis.json').read_text())   # doc 20 §6 inherited context
    assert {c['project'] for c in epay['dependencyContext']} == {'common-datasource', 'common-catalogue'}
    assert 'CAMEL2_VERSION' in epay['dependencyContext'][0]['findings']
    shared = json.loads((wsd / 'shared-findings.json').read_text())
    assert any(c['ruleId'] == 'JAKARTA_JMS' and len(c['projects']) == 5 and c['id'].startswith('COMMON_RULE-') for c in shared['commonRules'])
    rep = (wsd / 'workspace-analysis.md').read_text()
    assert 'MULTI_PROJECT_REPOSITORY' in rep and 'Aggregator POM:\nNOT PRESENT' in rep and 'Workspace Maven build:\nNOT APPLICABLE' in rep and 'Migration waves:\n3' in rep
    assert 'Projects discovered:\n5' in rep and 'Projects analyzed:\n5' in rep
    assert any(r['rule'] == 'CAMEL_2_WORKSPACE' and r['affectedProjects'] == 5 for r in shared['workspaceRules'])
    assert cli_tool.main(['analyze', str(base), '--discover-projects']) == 0
    assert json.loads(capsys.readouterr().out)['shared'] is None


def test_single_project_commands_ask_for_a_selection(tmp_path, monkeypatch, capsys):
    install(monkeypatch, FakeMaven(effective_ok=True))
    base = make_workspace(tmp_path / 'src')
    assert cli_tool.main(['plan', str(base), '--accept', ACCEPT]) == 4  # ROOT_SELECTION_REQUIRED, never a guessed root
    assert 'ROOT_SELECTION_REQUIRED' in capsys.readouterr().err
    assert cli_tool.main(['plan', str(base), '--project', 'common-activemq', '--accept', ACCEPT]) == 0


def test_migrate_workspace_by_waves_with_isolated_overlays(tmp_path, monkeypatch):
    fake = install(monkeypatch, FakeMaven(effective_ok=True))
    seen = []
    orig = fake.mvn

    def mvn(ctx, cwd, goals, name, timeout=None):
        from mf.worker import mavenops
        seen.append((name, str(mavenops.current_overlay() or '')))
        if name == 'baseline-compile' and 'common-catalogue' in str(cwd):
            return fake._result(ctx, name, 1, '[ERROR] /x/src/main/java/A.java:[3,17] cannot find symbol')  # breaks one project
        return orig(ctx, cwd, goals, name, timeout)
    monkeypatch.setattr('mf.worker.mavenops.mvn', mvn)
    base = make_workspace(tmp_path / 'src')
    before = content_hash(base)
    out = tmp_path / 'out'
    code = cli_tool.main(['migrate', str(base), '--workspace', '--output', str(out), '--accept', ACCEPT])
    state = json.loads((out / 'workspace' / 'workspace-state.json').read_text())
    res = {n: x['result'] for n, x in state['projects'].items()}
    assert code == 2 and content_hash(base) == before and state['workspaceStatus'] == 'PARTIAL_SUCCESS'          # M5
    assert res['common-datasource']['status'] in ('SUCCEEDED', 'PARTIAL_SUCCESS')
    assert res['common-catalogue']['status'] in ('BLOCKED', 'FAILED')    # its baseline does not compile
    assert res['common-epay'] == {'project': 'common-epay', 'status': 'BLOCKED', 'reason': 'BLOCKED_BY_DEPENDENCY',
                                  'blockedBy': ['common-catalogue']}  # M1: only the dependents, never FAILED when never attempted
    assert state['projects']['common-epay'] == {'analysis': 'ANALYSIS_SUCCEEDED', 'migration': 'BLOCKED', 'result': res['common-epay']}
    mw = json.loads((out / 'workspace' / 'migration-waves.json').read_text())
    assert [w['status'] for w in mw] == ['WAVE_SUCCEEDED', 'WAVE_FAILED', 'WAVE_BLOCKED']
    assert mw[2]['blocked'] == ['common-epay'] and mw[2]['runnable'] == []
    ws = json.loads((out / 'workspace' / 'workspace.json').read_text())
    assert ws['counts']['projectsAnalyzed'] == 5 and ws['analysisStatus'] == 'ANALYSIS_SUCCEEDED' and ws['migrationStatus'] == 'MIGRATION_PARTIAL_SUCCESS'
    assert (out / 'projects' / 'common-activemq' / 'migrated-project').exists() and not (out / 'projects' / 'common-epay' / 'migrated-project').exists()
    assert '⊘ common-epay: BLOCKED — bloqueado por common-catalogue' in (out / 'workspace' / 'workspace-migration.md').read_text()
    # baseline builds see legacy internal artifacts, candidates the migrated ones; the shared repository is never written
    assert any(n == 'install-common-activemq-migrated' and h.endswith('.m2-migrated') for n, h in seen)
    assert any(n == 'install-common-activemq-legacy' and h.endswith('.m2-legacy') for n, h in seen)
    assert any(n.startswith('candidate-compile') and h.endswith('.m2-migrated') for n, h in seen)
    assert not (out / '.m2-legacy').exists()
    import zipfile
    z = zipfile.ZipFile(out / 'migrated-workspace.zip')  # doc 19 §17: the whole workspace, not one project
    man = json.loads(z.read('.migration/manifest.json'))
    assert man['projects'] == 5 and man['projectStatus']['common-epay']['content'] == 'original (no migrado)'
    assert man['projectStatus']['common-activemq']['content'] == 'migrated' and man['status'] == 'PARTIAL_SUCCESS'
    tops = {n.split('/')[2] for n in z.namelist() if n.startswith('develop/commons/')}
    assert tops == set(LAYOUT) and not any('/target/' in n for n in z.namelist())


def test_overlay_mirror_lets_openrewrite_read_internal_artifacts(tmp_path):
    """Regression (real run): with mirrorOf='*' OpenRewrite's resolver mirrored the local overlay and could not find the
    migrated internal dependency. Only while an overlay is active the mirror is external:* (still every remote repository)."""
    import time
    from mf.worker import mavenops, runner
    ctx = runner.Context('t', tmp_path, deadline=time.monotonic() + 60)
    assert '<mirrorOf>*</mirrorOf>' in mavenops.settings_file(ctx).read_text()
    mavenops.use_local_overlay(tmp_path / 'head')
    try:
        assert '<mirrorOf>external:*</mirrorOf>' in mavenops.settings_file(ctx).read_text()
    finally:
        mavenops.use_local_overlay(None)


def test_platform_scan_of_a_workspace_and_project_selection(client, token, tmp_path, monkeypatch):
    fake = install(monkeypatch, FakeMaven(effective_ok=False))
    cwds = []
    orig = fake.mvn
    monkeypatch.setattr('mf.worker.mavenops.mvn', lambda ctx, cwd, goals, name, timeout=None: cwds.append((name, str(cwd))) or orig(ctx, cwd, goals, name, timeout))
    from mf.config import get_settings
    roots = get_settings().local_source_roots.split(',')[0].strip()
    from pathlib import Path
    base = make_workspace(Path(roots) / f'ws-{tmp_path.name}')
    try:
        pid = new_project(client, token, str(base))  # created: a workspace is not ROOT_AMBIGUOUS any more
        r = client.post(f'/api/v1/projects/{pid}/scans', headers=token('diego.dev'), json={}).json()
        run_job(r['jobId'])
        scan = client.get(f"/api/v1/scans/{r['scanId']}", headers=token('diego.dev')).json()
        assert scan['status'] == 'succeeded', scan
        ws = scan['summary']['workspace']
        assert ws['repositoryType'] == 'MULTI_PROJECT_REPOSITORY' and len(ws['projects']) == 5 and len(ws['order']['waves']) == 3
        assert ws['shared']['metrics']['Camel 2'] == '5/5'
        # doc 21: no Maven on the workspace root (it has no aggregator); one effective-POM run per ProjectContext
        eff = [c for n, c in cwds if n == 'maven-effective-pom']
        assert len(eff) == 5 and all(c.rstrip('/').split('/')[-1] in LAYOUT for c in eff)
        assert ws['workspaceBuild'] == {'status': 'SKIPPED', 'reason': 'WORKSPACE_BUILD_NOT_APPLICABLE', 'aggregatorPom': 'NOT PRESENT'}
        assert scan['mavenResolution'] == 'per-project' and all(p['mavenResolution'] == 'failed' for p in ws['projects'])
        assert ws['analysisStatus'] == 'ANALYSIS_SUCCEEDED' and ws['counts']['projectsAnalyzed'] == 5                    # doc 18 on the platform
        assert all(p['analysisStatus'] in ('PASS', 'WARN') for p in ws['projects'])
        names = {a['name'] for a in client.get(f'/api/v1/projects/{pid}/artifacts', headers=token('diego.dev'), params={'scanId': scan['id']}).json()['items']}
        assert {f'{n}.report.md' for n in LAYOUT} | {f'{n}.analysis.json' for n in LAYOUT} | {'workspace-analysis.md'} <= names
        prof = next(p for p in client.get('/api/v1/profiles', headers=token('ana.arquitecta')).json() if p['runtime'] == 'spring' and p['status'] == 'validated')
        nope = client.post(f"/api/v1/scans/{scan['id']}/plans", headers=token('ana.arquitecta'), json={'profileId': prof['id']})
        assert nope.status_code == 409 and nope.json()['code'] == 'ROOT_SELECTION_REQUIRED'
        assert client.post(f'/api/v1/projects/{pid}/scans', headers=token('diego.dev'), json={'projectRoot': '../x'}).status_code == 422
        r2 = client.post(f'/api/v1/projects/{pid}/scans', headers=token('diego.dev'),
                         json={'projectRoot': 'develop/commons/common-activemq'}).json()
        run_job(r2['jobId'])
        s2 = client.get(f"/api/v1/scans/{r2['scanId']}", headers=token('diego.dev')).json()
        assert s2['status'] == 'succeeded' and s2['summary']['workspace']['selectedProject'] == 'develop/commons/common-activemq'
        assert client.post(f"/api/v1/scans/{s2['id']}/plans", headers=token('ana.arquitecta'), json={'profileId': prof['id']}).status_code == 201

        # waves on the platform: every project, dependencies first, from the workspace scan
        url = f"/api/v1/scans/{scan['id']}/workspace-migrations"
        body = {'profileId': prof['id'], 'accept': {'CAMEL2_VERSION': 'Revisado contra la guía 2→3 para todo el workspace'}}
        assert client.post(url, headers=token('diego.dev'), json=body).status_code == 403          # developer: cannot accept findings
        need = client.post(url, headers=token('ana.arquitecta'), json={'profileId': prof['id']})   # Camel 2 without the 2->3 decision
        assert need.status_code == 409 and need.json()['code'] == 'DECISION_REQUIRED' and len(need.json()['details']['projects']) == 5
        assert client.post(f"/api/v1/scans/{s2['id']}/workspace-migrations", headers=token('ana.arquitecta'), json=body).status_code == 409
        acc = client.post(url, headers=token('ana.arquitecta'), json=body)
        assert acc.status_code == 202, acc.text
        assert client.post(url, headers=token('ana.arquitecta'), json=body).status_code == 409      # one at a time
        run_job(acc.json()['jobId'])
        wm = client.get(f"/api/v1/workspace-migrations/{acc.json()['workspaceMigrationId']}", headers=token('aldo.auditor')).json()
        assert wm['state'] == 'partial_success', wm['job']
        assert set(wm['results']) == set(LAYOUT) and all(r['status'] == 'PARTIAL_SUCCESS' for r in wm['results'].values())
        assert [w['projects'] for w in wm['summary']['migrationWaves']][-1] == ['common-epay'] and wm['summary']['counts']['projectsAnalyzed'] == 5
        epay = wm['results']['common-epay']['artifacts']
        epay_prev_ds = wm['results']['common-datasource']['artifacts']['migrated-project.zip']
        assert {'migrated-project.zip', 'migration-report.md', 'candidate.patch'} <= set(epay)
        zip_id = next(v for k, v in wm['summary']['artifacts'].items() if k.endswith('migrated-workspace.zip'))
        import io, zipfile
        z = zipfile.ZipFile(io.BytesIO(client.get(f'/api/v1/artifacts/{zip_id}/download', headers=token('aldo.auditor')).content))
        assert {n.split('/')[0] for n in z.namelist() if not n.startswith('.migration')} == set(LAYOUT)
        assert [m['id'] for m in client.get(url, headers=token('aldo.auditor')).json()] == [wm['id']]
        sm = wm['summary']                                                                                    # doc 20 on the platform
        assert sm['analysisStatus'] == 'ANALYSIS_SUCCEEDED' and sm['migrationStatus'] == 'MIGRATION_PARTIAL_SUCCESS'
        assert [w['wave'] for w in sm['analysisWaves']] == ['A1', 'A2', 'A3'] and [w['status'] for w in sm['migrationWaves']] == ['WAVE_SUCCEEDED'] * 3
        assert all(r['analysis'] == 'ANALYSIS_SUCCEEDED' for r in wm['results'].values())
        # retry: everything migrated -> nothing to resume; retrying one project reuses the others (never repeated)
        rurl = f"/api/v1/workspace-migrations/{wm['id']}/retry"
        assert client.post(rurl, headers=token('ana.arquitecta'), json={}).status_code == 409
        assert client.post(rurl, headers=token('ana.arquitecta'), json={'projects': ['nope']}).status_code == 422
        rr = client.post(rurl, headers=token('ana.arquitecta'), json={'projects': ['common-epay']})
        assert rr.status_code == 202, rr.text
        run_job(rr.json()['jobId'])
        w2 = client.get(f"/api/v1/workspace-migrations/{rr.json()['workspaceMigrationId']}", headers=token('aldo.auditor')).json()
        assert w2['state'] == 'partial_success' and w2['retryOf'] == wm['id'] and w2['retry'] == ['common-epay'], w2['job']
        assert not w2['results']['common-epay'].get('reused') and all(w2['results'][n].get('reused') for n in LAYOUT if n != 'common-epay')
        assert w2['results']['common-datasource']['artifacts']['migrated-project.zip'] == epay_prev_ds
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_one_failing_project_does_not_abort_the_workspace_analysis(tmp_path, monkeypatch):
    """Doc 18 case 2: projectsAnalyzed = 5, projectsFailed = 1, workspaceStatus = PARTIAL_SUCCESS."""
    from mf.orchestration import multi_project_orchestrator as orch
    base = make_workspace(tmp_path / 'src')
    real = cli_tool.analyze

    def flaky(src, *a, **kw):
        if src.name == 'common-datasource':
            raise RuntimeError('pom.xml ilegible')
        return real(src, *a, **kw)
    monkeypatch.setattr(cli_tool, 'analyze', flaky)
    out = tmp_path / 'out'
    res = cli_tool.analyze_workspace(base, out, False, lambda m: None)
    assert res['projectsAnalyzed'] == 5 and res['projectsFailed'] == 1 and res['analysisStatus'] == 'ANALYSIS_PARTIAL_SUCCESS'   # A2
    by = {p['project']: p for p in res['projects']}
    assert by['common-datasource']['analysis'] == 'ANALYSIS_FAILED' and by['common-datasource']['error'].startswith('PROJECT_ANALYSIS_FAILED')
    assert by['common-epay']['analysis'] == 'ANALYSIS_PARTIAL' and by['common-epay']['contextMissing'] == ['common-datasource']   # A3
    assert by['common-catalogue']['analysis'] == 'ANALYSIS_SUCCEEDED'  # independent of the failure: full context
    assert [w['status'] for w in res['analysisWaves']] == ['ANALYSIS_WAVE_PARTIAL_SUCCESS', 'ANALYSIS_WAVE_SUCCEEDED', 'ANALYSIS_WAVE_PARTIAL_SUCCESS']
    assert 'PROJECT_ANALYSIS_FAILED' in (out / 'projects' / 'common-datasource' / 'analysis' / 'report.md').read_text()
    assert 'Contexto incompleto' in (out / 'projects' / 'common-epay' / 'analysis' / 'report.md').read_text()
    ws = orch.build_context(base)
    assert [p.name for p in ws.projects] == sorted(LAYOUT) and all(p.analysis_status == 'PENDING' for p in ws.projects)


def test_engine_inherits_the_platform_job_context(tmp_path):
    """Cancelling or timing out the workspace job stops every engine step (same cancel event, deadline and live log)."""
    import time
    from mf.worker import runner
    job = runner.Context('job-1', tmp_path, deadline=time.monotonic() + 30, emit=lambda *a: None)
    assert cli_tool.engine_context(tmp_path / 'a', 1).job_id == 'cli'
    cli_tool._PARENT['ctx'] = job
    try:
        c = cli_tool.engine_context(tmp_path / 'b', 12)
        assert c.job_id == 'job-1' and c.cancel is job.cancel and c.deadline == job.deadline and c.emit is job.emit
    finally:
        cli_tool._PARENT['ctx'] = None


def test_retry_reuses_successful_projects_and_unblocks_dependents(tmp_path, monkeypatch):
    """Doc 20 §20-§21: retry one project; successful ones are not repeated; its blocked dependents become READY and run."""
    fake = install(monkeypatch, FakeMaven(effective_ok=True))
    orig, broken, calls = fake.mvn, {'on': True}, []

    def mvn(ctx, cwd, goals, name, timeout=None):
        calls.append((name, str(cwd)))
        if broken['on'] and name == 'baseline-compile' and 'common-datasource' in str(cwd):
            return fake._result(ctx, name, 1, '[ERROR] /x/src/main/java/A.java:[3,17] cannot find symbol')
        return orig(ctx, cwd, goals, name, timeout)
    monkeypatch.setattr('mf.worker.mavenops.mvn', mvn)
    base = make_workspace(tmp_path / 'src')
    out = tmp_path / 'out'
    assert cli_tool.main(['migrate', str(base), '--workspace', '--output', str(out), '--accept', ACCEPT]) == 2
    first = json.loads((out / 'workspace' / 'workspace-state.json').read_text())['projects']
    assert first['common-epay']['migration'] == 'BLOCKED' and first['common-catalogue']['migration'] in ('SUCCEEDED', 'PARTIAL_SUCCESS')
    broken['on'], calls[:] = False, []
    assert cli_tool.main(['migrate', str(base), '--workspace', '--output', str(out), '--accept', ACCEPT, '--retry', 'common-datasource']) == 0
    second = json.loads((out / 'workspace' / 'workspace-state.json').read_text())['projects']
    assert all(x['migration'] in ('SUCCEEDED', 'PARTIAL_SUCCESS') for x in second.values())   # epay: BLOCKED -> READY -> migrated
    assert second['common-catalogue']['result'].get('reused') and not second['common-epay']['result'].get('reused')
    rerun = {c for n, c in calls if n == 'baseline-compile'}
    assert any('common-datasource' in c for c in rerun) and any('common-epay' in c for c in rerun)
    assert not any('common-catalogue' in c or 'common-activemq' in c for c in rerun)        # successful projects never repeated
    assert any(n == 'install-common-catalogue-migrated' for n, _ in calls)                    # reused ones re-enter the overlays
    with pytest.raises(SystemExit):
        cli_tool.main(['migrate', str(base), '--workspace', '--output', str(out), '--retry', 'nope', '--bogus'])


def test_parallel_migration_inside_a_wave(tmp_path, monkeypatch):
    """Doc 20 §10: RUNNABLE projects of a wave migrate in parallel (Maven file locks on the shared overlays)."""
    import threading
    fake = install(monkeypatch, FakeMaven(effective_ok=True))
    orig, flags, threads = fake.mvn, [], set()

    def mvn(ctx, cwd, goals, name, timeout=None):
        from mf.worker import mavenops
        flags.append(mavenops._parallel['on'])
        threads.add(threading.get_ident())
        return orig(ctx, cwd, goals, name, timeout)
    monkeypatch.setattr('mf.worker.mavenops.mvn', mvn)
    out = tmp_path / 'out'
    assert cli_tool.main(['migrate', str(make_workspace(tmp_path / 'src')), '--workspace', '--output', str(out), '--accept', ACCEPT, '--parallel', '3']) == 0
    assert all(flags) and len(threads) > 1
    from mf.worker import mavenops
    assert mavenops._parallel['on'] is False


def test_workspace_root_is_never_a_build_target(tmp_path, monkeypatch, capsys):
    """Doc 21: MULTI_PROJECT_REPOSITORY != MAVEN_MULTI_MODULE; workspaceRoot != projectRoot."""
    import time
    from mf import preflight
    from mf.build.build_target_resolver import for_workspace
    from mf.worker import mavenops, runner
    base = make_workspace(tmp_path / 'src')
    root = base / 'develop' / 'commons'
    t = for_workspace(cli_tool.discover(base)['info'], root)
    assert t.type == 'NOT_APPLICABLE' and t.pom is None                                                      # cases 1 and 3
    started = []
    monkeypatch.setattr(runner, 'run', lambda args, cwd, ctx, **kw: started.append(args))
    res = mavenops.mvn(runner.Context('t', tmp_path, deadline=time.monotonic() + 60), root, ['validate'], 'oops')
    errs = preflight.classify(res.log_path.read_text())
    assert not started and errs[0]['category'] == 'ORCHESTRATION_ERROR' and 'MAVEN_EXECUTED_ON_NON_PROJECT_ROOT' in errs[0]['message']  # §15
    install(monkeypatch, FakeMaven(effective_ok=True))
    calls = []
    monkeypatch.setattr(cli_tool, 'run_validation', lambda ctx, proj, name, goals=('clean', 'test'): calls.append(proj.name) or ({'status': 'PASS', 'log': 'x.log'}, ''))
    assert cli_tool.main(['validate', str(base)]) == 0                                                       # case 2: one build per project
    assert len(calls) == 5 and 'WORKSPACE_BUILD_NOT_APPLICABLE' in capsys.readouterr().out

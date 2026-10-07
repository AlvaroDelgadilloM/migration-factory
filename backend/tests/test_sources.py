"""Project sources: local folder and ZIP work end to end without any Git URL, branch or credential;
Git keeps its restrictions. ZIP validation covers traversal, symlinks, bombs, size and structure."""
import io
import shutil
import uuid
import zipfile
from pathlib import Path

import pytest

from conftest import ROOT, TMP
from helpers import FakeMaven, install, new_project, run_job
from mf.config import get_settings
from mf.worker.source import content_hash, make_snapshot, restore_snapshot
from mf.security import UnsafeInput

API = '/api/v1'
PILOT = ROOT / 'examples' / 'pilot-orders'


def pilot_zip(wrapper='pilot-orders/') -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in sorted(PILOT.rglob('*')):
            if f.is_file() and 'target' not in f.parts:
                z.write(f, wrapper + f.relative_to(PILOT).as_posix())
        z.writestr('__MACOSX/._junk', 'x')
    return buf.getvalue()


def upload(client, token, data, user='ana.arquitecta', name='proyecto.zip'):
    return client.post(f'{API}/uploads', content=data, headers=token(user) | {'Content-Type': 'application/zip', 'X-Filename': name})


def crafted(entries) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for info, data in entries:
            z.writestr(info, data)
    return buf.getvalue()


def test_zip_upload_detects_root_and_modules(client, token):
    r = upload(client, token, pilot_zip())
    assert r.status_code == 201, r.text
    st = r.json()['structure']
    assert st['root'] == 'pilot-orders' and st['modules'] == ['orders-routes/pom.xml', 'pom.xml']
    assert len(r.json()['sha256']) == 64


@pytest.mark.parametrize('entries,code', [
    ([('../evil.txt', 'x'), ('pom.xml', '<project/>')], 'ZIP_PATH_TRAVERSAL'),
    ([('/abs/pom.xml', '<project/>')], 'ZIP_PATH_TRAVERSAL'),
    ([('a\\..\\b', 'x'), ('pom.xml', '<project/>')], 'ZIP_PATH_TRAVERSAL'),
    ([('README.md', 'sin maven')], 'NO_POM'),
    ([('pom.xml', '<project/>'), ('big.bin', b'\0' * 3_000_000)], 'ZIP_BOMB'),
])
def test_zip_upload_rejections(client, token, entries, code):
    r = upload(client, token, crafted(entries))
    assert r.status_code == 422 and r.json()['code'] == code, r.text


def test_zip_symlink_and_garbage_and_size(client, token, monkeypatch):
    link = zipfile.ZipInfo('link')
    link.external_attr = 0o120777 << 16
    r = upload(client, token, crafted([(link, '/etc/passwd'), ('pom.xml', '<project/>')]))
    assert r.json()['code'] == 'ZIP_SYMLINK'
    assert upload(client, token, b'not a zip').json()['code'] == 'ZIP_INVALID'
    monkeypatch.setattr(get_settings(), 'max_upload_mb', 0)
    assert upload(client, token, pilot_zip()).status_code == 413


def test_project_source_fields_validated_per_type(client, token, pilot_dir, monkeypatch):
    post = lambda body: client.post(f'{API}/projects', headers=token('ana.arquitecta'),
                                    json={'name': f'P {uuid.uuid4().hex[:5]}', 'targetRuntime': 'spring'} | body)
    assert post({'localPath': pilot_dir}).status_code == 422                                   # sourceType required
    assert post({'sourceType': 'local'}).status_code == 422                                    # path required
    assert post({'sourceType': 'local', 'localPath': pilot_dir, 'repositoryUrl': 'https://github.com/a/b.git'}).status_code == 422
    assert post({'sourceType': 'zip', 'localPath': pilot_dir}).status_code == 422
    assert post({'sourceType': 'git', 'repositoryUrl': f'file://{pilot_dir}'}).json()['code'] == 'GIT_URL_SCHEME'
    assert post({'sourceType': 'local', 'localPath': '/etc'}).json()['code'] == 'LOCAL_PATH_NOT_ALLOWED'
    assert post({'sourceType': 'local', 'localPath': pilot_dir + '/../../../etc'}).json()['code'] == 'LOCAL_PATH_NOT_ALLOWED'
    assert post({'sourceType': 'local', 'localPath': 'relative/path'}).json()['code'] == 'LOCAL_PATH_INVALID'
    assert post({'sourceType': 'zip', 'uploadId': str(uuid.uuid4())}).status_code == 404
    up = upload(client, token, pilot_zip(), user='diego.dev').json()
    assert post({'sourceType': 'zip', 'uploadId': up['id']}).status_code == 404               # someone else's upload
    monkeypatch.setattr(get_settings(), 'local_source_roots', '')
    r = post({'sourceType': 'local', 'localPath': pilot_dir})
    assert r.json()['code'] == 'LOCAL_SOURCE_DISABLED'
    caps = client.get(f'{API}/capabilities', headers=token('diego.dev')).json()
    assert caps['localSourceEnabled'] is False


def _flow_without_git(client, token, pid):
    """scan -> findings -> plan -> preview -> apply -> validations; returns (scan, execution)."""
    r = client.post(f'{API}/projects/{pid}/scans', headers=token('diego.dev'), json={})
    assert r.status_code == 202, r.text
    run_job(r.json()['jobId'])
    scan = client.get(f"{API}/scans/{r.json()['scanId']}", headers=token('diego.dev')).json()
    assert scan['status'] == 'succeeded', scan['job']
    assert scan['commitSha'] is None and len(scan['snapshotHash']) == 64
    prof = next(p for p in client.get(f'{API}/profiles', headers=token('ana.arquitecta')).json() if p['key'].startswith('spring') and p['version'] == 3)
    plan = client.post(f"{API}/scans/{scan['id']}/plans", headers=token('ana.arquitecta'), json={'profileId': prof['id']}).json()
    pinned = next(s for s in plan['steps'] if s['key'] == 'camel-3-to-4')['preconditions']
    assert any(p['code'] == 'SNAPSHOT_PINNED' and p['ok'] for p in pinned)
    f = client.get(f"{API}/scans/{scan['id']}/findings", headers=token('ana.arquitecta'), params={'rule': 'CAMEL2_VERSION'}).json()['items'][0]
    client.patch(f"{API}/findings/{f['id']}/review", headers=token('ana.arquitecta') | {'If-Match': str(f['version'])},
                 json={'decision': 'resolved', 'reason': 'Revisión 2→3 hecha para la fixture'})
    pv = client.post(f"{API}/plans/{plan['id']}/preview", headers=token('diego.dev')).json()
    run_job(pv['jobId'])
    assert client.get(f"{API}/executions/{pv['executionId']}", headers=token('diego.dev')).json()['state'] == 'succeeded'
    client.post(f"{API}/plans/{plan['id']}/approve", headers=token('ana.arquitecta') | {'If-Match': str(plan['rowVersion'])})
    ex = client.post(f"{API}/plans/{plan['id']}/executions", headers=token('diego.dev'), json={'mode': 'apply'}).json()
    run_job(ex['jobId'])
    execution = client.get(f"{API}/executions/{ex['executionId']}", headers=token('diego.dev')).json()
    assert execution['state'] == 'partial_success', execution['job']  # vm: manual action pending -> never plain success
    assert execution['summary']['status'] == 'PARTIAL_SUCCESS' and 'MANUAL_ACTIONS_PENDING' in execution['summary']['reasonCodes']
    assert execution['baseSha'] is None and execution['baseSnapshot'] == scan['snapshotHash']
    return scan, execution


def _candidate_zip(client, token, pid, execution_id):
    arts = client.get(f'{API}/projects/{pid}/artifacts', headers=token('aldo.auditor'), params={'executionId': execution_id}).json()['items']
    cz = next(a for a in arts if a['name'] == 'candidate.zip')
    data = client.get(f"{API}/artifacts/{cz['id']}/download", headers=token('aldo.auditor')).content
    return zipfile.ZipFile(io.BytesIO(data)), {a['name'] for a in arts}


def test_zip_source_full_flow_without_git(client, token, monkeypatch):
    install(monkeypatch, FakeMaven(effective_ok=True))
    up = upload(client, token, pilot_zip()).json()
    pid = new_project(client, token, up['id'], source_type='zip')
    project = client.get(f'{API}/projects/{pid}', headers=token('ana.arquitecta')).json()
    assert project['sourceType'] == 'zip' and project['repositoryUrl'] is None and project['credentialRef'] is None
    assert project['upload']['structure']['root'] == 'pilot-orders'
    scan, execution = _flow_without_git(client, token, pid)
    assert scan['projectRoot'] == 'pilot-orders'  # Maven root inside the ZIP; the snapshot starts there
    z, names = _candidate_zip(client, token, pid, execution['id'])
    assert {'candidate.patch', 'execution-report.html', 'execution-report.json'} <= names
    files = set(z.namelist())
    assert 'pom.xml' in files and 'orders-routes/src/main/java/demo/orders/Application.java' in files
    assert not any(n.startswith(('.git/', '__MACOSX')) or '/target/' in n for n in files)
    assert '<camel.version>4.14.0</camel.version>' in z.read('pom.xml').decode()
    assert any('solo están disponibles cuando el origen es un repositorio Git' in b for b in execution['prBlockers'])
    r = client.post(f"{API}/executions/{execution['id']}/pull-request", headers=token('rosa.revisora'))
    assert r.status_code == 409
    assert client.post(f'{API}/projects', headers=token('ana.arquitecta'),
                       json={'name': 'Reuse', 'sourceType': 'zip', 'uploadId': up['id'], 'targetRuntime': 'spring'}).json()['code'] == 'UPLOAD_IN_USE'


def test_local_source_uses_snapshot_not_live_folder(client, token, monkeypatch):
    install(monkeypatch, FakeMaven(effective_ok=True))
    folder = TMP / 'sources' / f'mutable-{uuid.uuid4().hex[:6]}'
    shutil.copytree(PILOT, folder)
    before = content_hash(folder)
    pid = new_project(client, token, str(folder))
    project = client.get(f'{API}/projects/{pid}', headers=token('ana.arquitecta')).json()
    assert project['sourceType'] == 'local' and project['localPath'] == str(folder.resolve())
    # the folder changes *after* the scan: plan and execution must still use the scanned snapshot
    orig_run = run_job

    scan_resp = client.post(f'{API}/projects/{pid}/scans', headers=token('diego.dev'), json={}).json()
    run_job(scan_resp["jobId"])
    scan = client.get(f"{API}/scans/{scan_resp['scanId']}", headers=token('diego.dev')).json()
    assert scan['snapshotHash'] == before
    (folder / 'orders-routes/src/main/java/demo/orders/Late.java').write_text('package demo.orders; class Late {}')
    prof = next(p for p in client.get(f'{API}/profiles', headers=token('ana.arquitecta')).json() if p['key'].startswith('spring') and p['version'] == 3)
    plan = client.post(f"{API}/scans/{scan['id']}/plans", headers=token('ana.arquitecta'), json={'profileId': prof['id']}).json()
    f = client.get(f"{API}/scans/{scan['id']}/findings", headers=token('ana.arquitecta'), params={'rule': 'CAMEL2_VERSION'}).json()['items'][0]
    client.patch(f"{API}/findings/{f['id']}/review", headers=token('ana.arquitecta') | {'If-Match': str(f['version'])},
                 json={'decision': 'resolved', 'reason': 'Revisión 2→3 hecha para la fixture'})
    client.post(f"{API}/plans/{plan['id']}/approve", headers=token('ana.arquitecta') | {'If-Match': str(plan['rowVersion'])})
    ex = client.post(f"{API}/plans/{plan['id']}/executions", headers=token('diego.dev'), json={'mode': 'apply'}).json()
    run_job(ex['jobId'])
    execution = client.get(f"{API}/executions/{ex['executionId']}", headers=token('diego.dev')).json()
    assert execution['state'] == 'partial_success' and execution['baseSnapshot'] == before
    z, _ = _candidate_zip(client, token, pid, execution['id'])
    assert 'orders-routes/src/main/java/demo/orders/Late.java' not in z.namelist()
    assert (folder / 'pom.xml').read_text() == (PILOT / 'pom.xml').read_text()  # the original folder is never written


def test_local_source_full_flow_without_git(client, token, pilot_dir, monkeypatch):
    install(monkeypatch, FakeMaven(effective_ok=True))
    pid = new_project(client, token, pilot_dir)
    _, execution = _flow_without_git(client, token, pid)
    vals = {(v['suite'], v['target']): v['status'] for v in client.get(f"{API}/executions/{execution['id']}/validations", headers=token('diego.dev')).json()}
    assert vals[('compile', 'candidate')] == 'PASS' and vals[('test', 'candidate')] == 'PASS' and vals[('compile', 'baseline')] == 'PASS'


def test_git_source_records_commit_and_snapshot(client, token, git_source, monkeypatch):
    install(monkeypatch, FakeMaven(effective_ok=True))
    pid = new_project(client, token, git_source, source_type='git')
    r = client.post(f'{API}/projects/{pid}/scans', headers=token('diego.dev'), json={'ref': 'main'}).json()
    run_job(r['jobId'])
    scan = client.get(f"{API}/scans/{r['scanId']}", headers=token('diego.dev')).json()
    assert scan['status'] == 'succeeded', scan['job']
    assert len(scan['commitSha']) == 40 and len(scan['snapshotHash']) == 64 and scan['ref'] == 'main'
    assert scan['snapshotHash'] == content_hash(PILOT)  # same content as the local/zip sources


def test_snapshot_roundtrip_and_tamper_detection(tmp_path):
    out = tmp_path / 's.tar.gz'
    h = make_snapshot(PILOT, out)
    restore_snapshot(out, tmp_path / 'r', h)
    assert content_hash(tmp_path / 'r') == h
    with pytest.raises(UnsafeInput) as e:
        restore_snapshot(out, tmp_path / 'r2', '0' * 64)
    assert e.value.code == 'SNAPSHOT_MISMATCH'
    assert make_snapshot(PILOT, tmp_path / 's2.tar.gz') == h and (tmp_path / 's2.tar.gz').read_bytes() != b''


def test_server_side_folder_browser(client, token, pilot_dir, monkeypatch):
    import os
    get = lambda **p: client.get(f'{API}/sources/browse', headers=token('diego.dev'), params=p)
    roots = get().json()
    assert roots['path'] is None and roots['entries'][0]['path'] == str((TMP / 'sources').resolve())
    base = Path(roots['entries'][0]['path'])
    (base / '.hidden').mkdir(exist_ok=True)
    if not (base / 'escape').exists():
        os.symlink('/etc', base / 'escape')
    listing = get(path=str(base)).json()
    names = {e['name']: e for e in listing['entries']}
    assert names['pilot-orders']['hasPom'] and '.hidden' not in names and 'escape' not in names
    assert listing['parent'] is None  # cannot go above an authorized root
    sub = get(path=str(base / 'pilot-orders')).json()
    assert sub['hasPom'] and sub['parent'] == str(base) and {e['name'] for e in sub['entries']} == {'orders-routes'}
    assert get(path='/etc').json()['code'] == 'LOCAL_PATH_NOT_ALLOWED'
    assert get(path=str(base) + '/../..').json()['code'] == 'LOCAL_PATH_NOT_ALLOWED'
    assert get(path=str(base / 'escape')).json()['code'] == 'LOCAL_PATH_NOT_ALLOWED'  # symlink resolved outside the root
    assert client.get(f'{API}/sources/browse').status_code == 401
    monkeypatch.setattr(get_settings(), 'local_source_roots', '')
    assert get().json()['code'] == 'LOCAL_SOURCE_DISABLED'


def test_located_transform_self_check():
    import subprocess, sys
    r = subprocess.run([sys.executable, '-m', 'mf.transform'], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == 'ok', r.stderr


def test_multi_project_zip_is_a_workspace_not_an_error(client, token):
    """Doc 17: several POMs without an aggregator -> MULTI_PROJECT_REPOSITORY (was rejected as ROOT_AMBIGUOUS)."""
    r = upload(client, token, crafted([('commons/a/pom.xml', '<project/>'), ('commons/b/pom.xml', '<project/>')]))
    assert r.status_code == 201, r.text
    st = r.json()['structure']
    assert st['repositoryType'] == 'MULTI_PROJECT_REPOSITORY' and st['root'] == 'commons' and st['projects'] == ['a', 'b']

"""Pruebas (manual and automatic HTTP tests, docs/19). Only API + mf.tomcat_war.http_run: no worker import, so it also
runs where the POSIX worker cannot (Windows)."""
import json
import threading
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mf.config import get_settings
from mf.security import UnsafeInput, validate_test_url
from mf.tomcat_war import http_run

API = '/api/v1'


def server(answer):
    class H(BaseHTTPRequestHandler):
        def _reply(self):
            n = int(self.headers.get('Content-Length') or 0)
            status, ctype, body = answer(self.command, self.path, dict(self.headers), self.rfile.read(n).decode())
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.end_headers()
            self.wfile.write(body.encode())
        do_GET = do_POST = _reply

        def log_message(self, *a):
            pass
    s = ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f'http://127.0.0.1:{s.server_address[1]}'


@pytest.fixture(scope='module')
def systems():
    def old(method, path, headers, body):
        if 'Envelope' in body:  # SOAP: same answer from both
            return 200, 'text/xml', '<soap:Envelope><soap:Body><ok/></soap:Body></soap:Envelope>'
        return (404, 'text/plain', 'no') if path.endswith('/missing') else (200, 'application/json', '{"points": 801}')

    def new(method, path, headers, body):
        if 'Envelope' in body:
            return 200, 'text/xml', '<soap:Envelope><soap:Body><ok/></soap:Body></soap:Envelope>'
        return 200, 'application/json', '{"points": 801}'
    a, ua = server(old)
    b, ub = server(new)
    yield ua, ub
    a.shutdown()
    b.shutdown()


@pytest.fixture(scope='module')
def project(pilot_dir):
    """Owned by ana.arquitecta. Created in the database: the source type does not matter here and the local-folder rules
    of the API expect a POSIX path."""
    from mf import db as dbmod
    from mf.api.projects import default_org
    from mf.models import Membership, Project, Repository
    with dbmod.session() as db:
        repo = Repository(source_type='local', provider='local', local_path=pilot_dir)
        db.add(repo)
        db.flush()
        p = Project(organization_id=default_org(db).id, name=f'Pruebas {uuid.uuid4().hex[:5]}', repository_id=repo.id, default_branch='-',
                    target_runtime='spring', created_by='ana.arquitecta')
        db.add(p)
        db.flush()
        db.add(Membership(project_id=p.id, subject_id='ana.arquitecta', role='owner'))
        db.commit()
        return str(p.id)


def test_only_allowed_systems_can_be_called(monkeypatch):
    monkeypatch.setattr(get_settings(), 'test_allowed_hosts', 'localhost,jboss-qa.cliente.local:8181')
    assert validate_test_url('http://localhost:18080/') == 'http://localhost:18080'
    assert validate_test_url('https://jboss-qa.cliente.local:8181/cxf') == 'https://jboss-qa.cliente.local:8181/cxf'
    for url, code in (('http://jboss-qa.cliente.local:9999', 'TEST_HOST_NOT_ALLOWED'), ('http://169.254.169.254/latest', 'TEST_HOST_NOT_ALLOWED'),
                      ('file:///etc/passwd', 'TEST_URL_SCHEME'), ('http://user:pw@localhost', 'TEST_URL_CREDENTIALS'),
                      ('http://localhost/a?x=1', 'TEST_URL_INVALID'), ('http://localhost/a b', 'TEST_URL_INVALID')):
        with pytest.raises(UnsafeInput) as e:
            validate_test_url(url)
        assert e.value.code == code, url


def test_manual_request_to_one_or_two_systems(client, token, project, systems):
    old, new = systems
    send = lambda body, user='ana.arquitecta': client.post(f'{API}/projects/{project}/http-tests/manual', headers=token(user), json=body)
    r = send({'request': {'name': 'puntos', 'path': '/points/1'}, 'targets': [{'label': 'JBoss', 'url': old}]})
    assert r.status_code == 200, r.text
    assert r.json()['results'][0] | {'body': None} == {'label': 'JBoss', 'url': old, 'status': 200, 'type': 'application/json',
                                                      'body': None, 'truncated': False} and 'state' not in r.json()
    r = send({'request': {'name': 'sin dato', 'path': '/missing'}, 'targets': [{'label': 'JBoss', 'url': old}, {'label': 'Tomcat', 'url': new}]}).json()
    assert r['state'] == 'DIFFERENT' and r['kind'] == 'DATA' and r['issues'][0] == 'HTTP 404 -> 200'
    soap = {'name': 'soap', 'method': 'POST', 'path': '/cxf/x', 'body': '<soap:Envelope><soap:Body><q/></soap:Body></soap:Envelope>',
            'contentType': 'text/xml; charset=UTF-8', 'headers': {'SOAPAction': '"urn:q"'}}
    assert send({'request': soap, 'targets': [{'label': 'JBoss', 'url': old}, {'label': 'Tomcat', 'url': new}]}).json()['state'] == 'SAME'
    bad = send({'request': {'name': 'x', 'path': '/'}, 'targets': [{'label': 'Fuera', 'url': 'http://10.1.2.3:8080'}]})
    assert bad.status_code == 422 and bad.json()['code'] == 'TEST_HOST_NOT_ALLOWED'
    assert send({'request': {'name': 'x', 'path': '/', 'headers': {'X-A': 'a\r\nInjected: 1'}}, 'targets': [{'label': 'J', 'url': old}]}).status_code == 422
    assert send({'request': {'name': 'x', 'path': '/'}, 'targets': [{'label': 'J', 'url': old}]}, user='mallory.externa').status_code == 404  # not a member: the project is not revealed


def test_automatic_run_is_queued_and_never_returns_credentials(client, token, project, systems):
    old, new = systems
    body = {'source': 'urls', 'name': 'member-inquiry', 'original': {'label': 'JBoss', 'url': old}, 'migrated': {'label': 'Tomcat', 'url': new},
            'cases': [{'name': 'puntos', 'path': '/points/1', 'headers': {'Authorization': 'Bearer secreto', 'memberNumber': '1'}}]}
    r = client.post(f'{API}/projects/{project}/http-tests/runs', headers=token('ana.arquitecta') | {'Idempotency-Key': str(uuid.uuid4())}, json=body)
    assert r.status_code == 202 and r.json()['status'] == 'queued', r.text
    run_id = r.json()['httpTestRunId']
    again = client.post(f'{API}/projects/{project}/http-tests/runs', headers=token('ana.arquitecta'), json=body | {'name': 'otra'})
    assert again.status_code == 409  # one run at a time per project
    listed = client.get(f'{API}/projects/{project}/http-tests/runs', headers=token('ana.arquitecta')).json()
    assert listed[0]['id'] == run_id and listed[0]['job']['kind'] == 'http_compare'
    assert listed[0]['cases'][0]['headers'] == {'Authorization': '***', 'memberNumber': '1'} and 'secreto' not in json.dumps(listed)
    assert client.get(f'{API}/http-tests/runs/{run_id}', headers=token('mallory.externa')).status_code == 404
    bad = client.post(f'{API}/projects/{project}/http-tests/runs', headers=token('ana.arquitecta'),
                      json=body | {'migrated': {'label': 'Tomcat', 'url': 'http://intranet.cliente:8080'}})
    assert bad.status_code == 422


def test_run_compares_both_systems_and_packs_the_evidence(tmp_path, systems):
    old, new = systems
    cases = [{'name': 'puntos', 'path': '/points/1', 'headers': {'Cookie': 'sesion=1'}}, {'name': 'sin dato', 'path': '/missing'}]
    s = http_run.run({'label': 'JBoss', 'url': old}, {'label': 'Tomcat', 'url': new}, cases, tmp_path, screenshots=False)
    assert s['status'] == 'FAIL' and s['totals']['same'] == 1 and s['totals']['different'] == 1 and s['differencesByKind'] == {'DATA': 1}
    assert [c['state'] for c in s['cases']] == ['SAME', 'DIFFERENT'] and s['cases'][0]['request']['headers'] == {'Cookie': '***'}
    assert s['cases'][1]['original'] == {'status': 404, 'type': 'text/plain', 'body': 'no', 'truncated': False}
    names = zipfile.ZipFile(tmp_path / 'evidence.zip').namelist()
    assert 'evidencia/index.html' in names and 'evidencia/caso-002.html' in names
    page = zipfile.ZipFile(tmp_path / 'evidence.zip').read('evidencia/caso-001.html').decode()
    assert 'JBoss' in page and 'Tomcat' in page and 'sesion=1' not in page


def test_run_from_a_migration_needs_its_war_and_a_valid_data_package(client, token, project):
    import io
    import zipfile as zf
    from mf import db as dbmod
    from sqlalchemy import select
    from mf.models import RuleCatalog, Scan, WorkspaceMigration
    with dbmod.session() as db:
        scan = Scan(project_id=uuid.UUID(project), ref='-', status='succeeded', created_by='ana.arquitecta',
                    catalog_id=db.scalar(select(RuleCatalog.id)))
        db.add(scan)
        db.flush()
        wm = WorkspaceMigration(project_id=uuid.UUID(project), scan_id=scan.id, target='tomcat-war', java='21', accepted={}, approved=[],
                                created_by='ana.arquitecta', state='succeeded', config={'containers': {'names': ['mobile', 'web1']}},
                                summary={'build': {'status': 'PASS'}, 'artifacts': {}})
        db.add(wm)
        db.commit()
        wm_id = str(wm.id)
    auth = token('ana.arquitecta')
    src = client.get(f'{API}/projects/{project}/http-tests/sources', headers=auth).json()
    assert src[0]['workspaceMigrationId'] == wm_id and src[0]['containers'] == ['mobile', 'web1'] and src[0]['war'] is False
    r = client.post(f'{API}/projects/{project}/http-tests/runs', headers=auth, json={'workspaceMigrationId': wm_id, 'container': 'mobile'})
    assert r.status_code == 409 and 'WAR' in r.json()['message']  # nothing to start
    assert client.post(f'{API}/projects/{project}/http-tests/runs', headers=auth, json={}).status_code == 422

    def pkg(files):
        b = io.BytesIO()
        with zf.ZipFile(b, 'w') as z:
            for n, t in files.items():
                z.writestr(n, t)
        return b.getvalue()
    up = lambda data: client.post(f'{API}/projects/{project}/http-tests/data-packages', headers=auth | {'X-Filename': 'datos.zip'}, content=data)
    ok = up(pkg({'datos/etc/com.x/app.properties': 'a=1\n', 'datos/sql/01.sql': 'create table t(x int);', 'datos/valores.json': '{"id": "7"}',
                 'datos/mocks.json': '[{"key": "ws.x", "status": 200, "body": "<ok/>"}]'}))
    assert ok.status_code == 201, ok.text
    assert ok.json()['contents'] == {'etc': ['com.x/app.properties'], 'sql': ['01.sql'], 'mocks': 1, 'values': 1, 'cases': 0}
    assert up(pkg({'../etc/x.properties': 'a=1'})).json()['code'] == 'DATA_PACKAGE_INVALID'
    assert up(pkg({'programa.exe': 'MZ'})).json()['code'] == 'DATA_PACKAGE_INVALID'
    assert up(b'no es zip').status_code == 422


def test_an_automatic_test_of_a_migration_becomes_its_g4_response_evidence():
    """docs/19: the responses half of G4 comes from Pruebas > Automáticas by itself; a smoke test keeps G4 in review."""
    import types
    import uuid as _uuid
    from mf.worker.tasks import _register_responses, g4_status
    assert g4_status({'routeStructure': {'status': 'PASS'}, 'responses': {'status': 'PASS', 'mode': 'DATOS'}}) == 'PASS'
    assert g4_status({'routeStructure': {'status': 'PASS'}, 'responses': {'status': 'PASS', 'mode': 'HUMO'}}) == 'REVIEW'
    assert g4_status({'routeStructure': {'status': 'PASS'}, 'responses': {'status': 'FAIL', 'mode': 'DATOS'}}) == 'FAIL'
    assert g4_status({'routeStructure': {'status': 'FAIL'}}) == 'FAIL'
    assert g4_status({'routeStructure': {'status': 'PASS'}}) == 'REVIEW'                    # no responses yet
    wm = types.SimpleNamespace(summary={'target': 'tomcat-war', 'equivalence': {'routeStructure': {'status': 'PASS'}, 'g4': 'REVIEW'}})
    db = types.SimpleNamespace(get=lambda model, key: wm)
    run = types.SimpleNamespace(id=_uuid.uuid4(), workspace_migration_id=_uuid.uuid4())
    summary = {'status': 'PASS', 'mode': 'DATOS', 'container': 'mobile', 'generatedCases': 3,
               'totals': {'cases': 3, 'same': 3, 'different': 0, 'accepted': 0, 'notExecutable': 0}}
    _register_responses(db, run, summary, 'ev-1')
    eq = wm.summary['equivalence']
    assert eq['g4'] == 'PASS' and eq['responses']['evidence'] == 'ev-1' and eq['responses']['httpTestRunId'] == str(run.id)
    _register_responses(db, run, summary | {'mode': 'HUMO'}, 'ev-2')                        # the latest run is the evidence
    assert wm.summary['equivalence']['g4'] == 'REVIEW' and 'humo' in wm.summary['equivalence']['responses']['detail']

"""Pruebas > Automáticas of a migrated project (mf.runtime_test). The real runs (JBoss example vs WildFly: Spring Boot, Tomcat
10.1, Tomcat 11 + Java 25, Quarkus) are recorded in ESTADO; here the detection, the safety checks and the worker flow with a
fake app."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mf import runtime_test
from test_purge import _applied

API = '/api/v1'
YML = ('camel:\n  servlet:\n    mapping:\n      context-path: "/api/*"\nserver:\n  servlet:\n    context-path: "/shop"\n'
       'spring:\n  datasource:\n    url: "${MF_DS_SHOPDS_URL}"\n    username: "${MF_DS_SHOPDS_USERNAME}"\n    password: "${MF_DS_SHOPDS_PASSWORD}"\n'
       'services:\n  erp:\n    url: "${SERVICES_ERP_URL:http://localhost:9/erp}"\n')


def project(tmp_path, pom='<project><packaging>jar</packaging></project>'):
    p = tmp_path / 'app'
    (p / 'src/main/resources').mkdir(parents=True)
    (p / 'pom.xml').write_text(pom)
    (p / 'src/main/resources/application.yml').write_text(YML)
    return p


def test_variables_runtime_and_base_path(tmp_path):
    p = project(tmp_path)
    v = {x['name']: x for x in runtime_test.variables(p)}
    assert list(v) == ['MF_DS_SHOPDS_PASSWORD', 'MF_DS_SHOPDS_URL', 'MF_DS_SHOPDS_USERNAME', 'SERVICES_ERP_URL']   # required first
    assert v['MF_DS_SHOPDS_PASSWORD']['secret'] and not v['MF_DS_SHOPDS_URL']['secret']
    assert not v['SERVICES_ERP_URL']['required'] and v['SERVICES_ERP_URL']['default'] == 'http://localhost:9/erp'
    assert 'URL JDBC' in v['MF_DS_SHOPDS_URL']['hint']
    assert runtime_test.runtime(p) == 'spring' and runtime_test.base_path('spring', p) == '/shop/api'
    assert runtime_test.base_path('tomcat', p, 'shop') == '/shop/api'                    # Tomcat: the WAR name is the context
    (p / 'pom.xml').write_text('<project><dependency><groupId>io.quarkus</groupId></dependency></project>')
    assert runtime_test.runtime(p) == 'quarkus'


def test_secrets_only_by_reference_and_only_test_hosts(tmp_path):
    spec = runtime_test.variables(project(tmp_path))
    ok = lambda h: h == 'db-test'  # noqa: E731
    given = {'MF_DS_SHOPDS_URL': {'value': 'jdbc:postgresql://db-test:5432/shop'}, 'MF_DS_SHOPDS_USERNAME': {'value': 'shop'},
             'MF_DS_SHOPDS_PASSWORD': {'ref': 'env:MF_CRED_TEST_DB'}}
    env, secrets = runtime_test.resolve(spec, given, ok, lambda r: 's3cret')
    assert env['MF_DS_SHOPDS_PASSWORD'] == 's3cret' and secrets == ['s3cret'] and 'SERVICES_ERP_URL' not in env   # default kept
    env, _ = runtime_test.resolve(spec, given | {'QUARKUS_DATASOURCE_JDBC_URL': {'value': 'jdbc:postgresql://db-test/x'}}, ok, lambda r: 's')
    assert env['QUARKUS_DATASOURCE_JDBC_URL'] == 'jdbc:postgresql://db-test/x'                # extra variables: same rules
    for bad, why in (({'QUARKUS_DATASOURCE_PASSWORD': {'value': 'x'}}, 'es un secreto'),
                     ({'SPRING_DATASOURCE_URL': {'value': 'jdbc:oracle:thin:@10.136.51.71:1521/X'}}, 'MF_TEST_ALLOWED_HOSTS'),({'MF_DS_SHOPDS_PASSWORD': {'value': 'shop'}}, 'es un secreto'),
                     ({'MF_DS_SHOPDS_URL': {'value': 'jdbc:oracle:thin:@10.136.51.71:1521/PRODBANREG'}}, 'MF_TEST_ALLOWED_HOSTS'),
                     ({'MF_DS_SHOPDS_USERNAME': None}, 'Falta')):
        g = {k: v for k, v in (given | bad).items() if v is not None}
        try:
            runtime_test.resolve(spec, g, ok, lambda r: 's3cret')
            raise AssertionError(bad)
        except runtime_test.RuntimeTestError as e:
            assert why in str(e)


class FakeApp:
    """The migrated app as the worker would start it: an HTTP server and a Camel startup log."""
    def __init__(self, logf: Path):
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                code = 200 if self.path.startswith('/shop/api/products') else 500
                body = b'[{"id":1}]' if code == 200 else b'boom'
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        logf.parent.mkdir(parents=True, exist_ok=True)
        logf.write_text('Apache Camel 4.14.0 (camel-1) started in 1s\n  Started products (rest://get:/products)\n'
                        '  Started get-customer (rest://get:/customers:/%7Bid%7D)\n')
        self.returncode = None

    def poll(self):
        return None


def test_worker_starts_maps_and_smoke_tests_the_migrated_app(client, token, pilot_repo, monkeypatch, tmp_path):
    from helpers import run_job
    from mf.api import http_tests
    from mf.tomcat_war import autotest
    pid, _, eid = _applied(client, token, pilot_repo, monkeypatch)
    spec = client.get(f'{API}/executions/{eid}/runtime-test', headers=token('ana.arquitecta'))
    assert spec.status_code == 200 and spec.json()['runtime'] in ('spring', 'tomcat'), spec.text
    spec_vars = runtime_test.variables(project(tmp_path))  # the pilot has no datasource: the variables of a JBoss-like app
    monkeypatch.setattr(http_tests, 'candidate_spec', lambda db, ex: {'runtime': 'spring', 'variables': spec_vars, 'basePath': '/shop/api'})
    monkeypatch.setattr(runtime_test, 'variables', lambda root: spec_vars)
    monkeypatch.setattr(runtime_test, 'base_path', lambda kind, root, war='': '/shop/api')
    apps = []

    def start(kind, root, work, port, env, java, log, mvn=None):
        app = FakeApp(work / 'logs' / 'app.log')
        apps.append(env)
        return app, work / 'logs' / 'app.log', f'http://127.0.0.1:{app.server.server_address[1]}'
    monkeypatch.setattr(runtime_test, 'start', start)
    monkeypatch.setattr(autotest, '_stop', lambda p: None)
    from mf.worker import mavenops, runner

    def mvn(ctx, cwd, goals, name, timeout=None, java_home=None):  # the build itself is Maven's job: covered by the real runs
        log = ctx.workdir / 'logs' / f'{name}.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text('BUILD SUCCESS')
        return runner.Result(0, 0, log)
    monkeypatch.setattr(mavenops, 'mvn', mvn)
    monkeypatch.setenv('MF_CRED_TEST_DB', 'pw-from-ref')
    from mf.worker import tasks
    monkeypatch.setitem(tasks.CREDENTIALS, 'MF_CRED_TEST_DB', 'pw-from-ref')
    body = {'source': 'execution', 'executionId': eid, 'screenshots': False, 'params': {'id': '42'},
            'variables': {'MF_DS_SHOPDS_URL': {'value': 'jdbc:postgresql://localhost:5432/shop'}, 'MF_DS_SHOPDS_USERNAME': {'value': 'shop'},
                          'MF_DS_SHOPDS_PASSWORD': {'ref': 'env:MF_CRED_TEST_DB'}}}
    url = f'{API}/projects/{pid}/http-tests/runs'
    bad = client.post(url, headers=token('ana.arquitecta'), json=body | {'variables': body['variables'] | {'MF_DS_SHOPDS_PASSWORD': {'value': 'x'}}})
    assert bad.status_code == 422 and bad.json()['code'] == 'VARIABLES_INVALID'
    r = client.post(url, headers=token('ana.arquitecta'), json=body)
    assert r.status_code == 202, r.text
    run_job(r.json()['jobId'])
    run = client.get(f"{API}/http-tests/runs/{r.json()['httpTestRunId']}", headers=token('ana.arquitecta')).json()
    s = run['summary']
    assert apps, run['job']
    assert apps[0]['MF_DS_SHOPDS_PASSWORD'] == 'pw-from-ref'                              # the reference reached the app only
    assert 'pw-from-ref' not in json.dumps(run) and run['variables']['MF_DS_SHOPDS_PASSWORD'] == {'ref': 'env:MF_CRED_TEST_DB'}
    assert s['mode'] == 'SMOKE' and [(e['method'], e['path']) for e in s['endpoints']] == [('GET', '/products'), ('GET', '/customers/{id}')]
    states = {c['path']: c['state'] for c in s['cases']}
    assert states == {'/products': 'SAME', '/customers/42': 'DIFFERENT'} and s['status'] == 'DIFFERENCES'   # the 500 is reported

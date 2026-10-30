"""Comparative load test (mf.tomcat_war.perf): same cases, same concurrency, one side after the other."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mf.tomcat_war import perf


def server(delay: float = 0.0, status: int = 200):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(delay)
            body = b'{"ok": true}'
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    s = ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f'http://127.0.0.1:{s.server_address[1]}'


CASES = [{'name': 'puntos', 'path': '/points/1'}, {'name': 'perfil', 'path': '/member/1'}]


def test_the_same_load_measures_latency_throughput_and_errors():
    s, url = server(delay=0.01)
    try:
        r = perf.load(url, CASES, users=4, seconds=2, warmup=1)
    finally:
        s.shutdown()
    assert r['requests'] > 20 and r['throughput'] > 10 and r['errors'] == 0 and r['statuses'] == {'200': r['requests']}
    assert 10 <= r['p50'] <= r['p95'] <= r['p99'] <= r['max']
    assert {name for name, _ in r['slowestCases']} == {'puntos', 'perfil'}


def test_a_slower_or_failing_migrated_side_is_not_a_pass(tmp_path):
    fast, slow, broken = server(0.005), server(0.08), server(0.005, status=500)
    try:
        same = perf.run({'url': fast[1]}, {'url': fast[1]}, CASES, tmp_path / 'a', 4, 2, 1, 0.2, 'HUMO')
        slower = perf.run({'url': fast[1]}, {'url': slow[1]}, CASES, tmp_path / 'b', 4, 2, 1, 0.2, 'HUMO')
        failing = perf.compare(perf.load(fast[1], CASES, 2, 1, 0), perf.load(broken[1], CASES, 2, 1, 0), 0.2)
    finally:
        for s, _ in (fast, slow, broken):
            s.shutdown()
    assert same['comparison']['status'] != 'FAIL'   # identical sides; PASS or, on a busy machine, REVIEW from timing noise
    assert slower['comparison']['status'] == 'FAIL' and any(r.startswith('p95') for r in slower['comparison']['reasons'])
    assert failing['status'] == 'FAIL' and failing['reasons'][0].startswith('más errores')
    saved = json.loads((tmp_path / 'b/reports/performance.json').read_text(encoding='utf-8'))
    assert saved['comparison']['status'] == 'FAIL' and 'no mide la capacidad de producción' in saved['scope']
    assert '| p95 |' in (tmp_path / 'b/reports/performance.md').read_text(encoding='utf-8')
    assert '## Por servicio' in (tmp_path / 'b/reports/performance.md').read_text(encoding='utf-8')
    assert {r['name'] for r in saved['byCase']} == {'puntos', 'perfil'} and all(r['deltaMs'] > 0 for r in saved['byCase'])


def test_the_service_that_makes_a_regression_comes_first():
    """Posadas, wave 1 on the platform: -25 % throughput and only totals in the report. Each service on both sides tells which one."""
    side = lambda **p50: {'perCase': {n: {'n': 100, 'p50': v, 'p95': v * 3} for n, v in p50.items()}}
    rows = perf.by_case(side(puntos=10.0, perfil=12.0, catalogo=8.0), side(puntos=11.0, perfil=40.0, catalogo=7.5))
    assert [r['name'] for r in rows] == ['perfil', 'puntos', 'catalogo']
    assert rows[0]['deltaMs'] == 28.0 and rows[0]['ratio'] == 3.33 and rows[0]['p95'] == [36.0, 120.0]


def test_the_performance_option_only_applies_to_a_migration_and_is_bounded():
    import uuid

    import pytest
    from pydantic import ValidationError

    from mf.schemas import HttpTestRunIn
    run = HttpTestRunIn(workspace_migration_id=uuid.uuid4(), performance={'users': 20, 'seconds': 120})
    assert run.performance.users == 20 and run.performance.seconds == 120
    assert HttpTestRunIn(workspace_migration_id=uuid.uuid4(), performance={}).performance.users == 10   # defaults
    with pytest.raises(ValidationError):
        HttpTestRunIn(workspace_migration_id=uuid.uuid4(), performance={'users': 500})               # bounded
    with pytest.raises(ValidationError, match='solo está disponible al probar una migración'):
        HttpTestRunIn(source='urls', name='x', original={'label': 'a', 'url': 'http://127.0.0.1:1'},
                      migrated={'label': 'b', 'url': 'http://127.0.0.1:2'}, cases=[{'name': 'c', 'path': '/a'}], performance={})


def test_cases_that_wait_on_an_external_system_are_left_out_and_listed(tmp_path):
    """Posadas smoke test: services that wait on a simulated external system took 26-30 s and swamped the measurement."""
    import xml.dom.minidom
    fast, slow = server(0.005), server(0.6)
    try:
        cases = [{'name': 'rapido', 'path': '/a'}]
        keep, out = perf.probe(fast[1], fast[1], cases + [{'name': 'externo', 'path': '/b'}], slow_ms=10_000)
        assert [c['name'] for c in keep] == ['rapido', 'externo'] and out == []
        keep, out = perf.probe(fast[1], slow[1], cases, slow_ms=300)
        assert keep == [] and 'sistema externo' in out[0]['reason']
        res = perf.run({'url': fast[1]}, {'url': slow[1]}, cases, tmp_path, 2, 1, 0, 0.2, 'HUMO', slow_ms=300)
    finally:
        fast[0].shutdown(), slow[0].shutdown()
    assert res['comparison']['status'] == 'NOT_EXECUTABLE' and res['cases'] == 0 and res['casesTotal'] == 1
    plan = (tmp_path / 'reports/performance-plan.jmx').read_text(encoding='utf-8')   # written even when nothing is measurable here
    assert len(xml.dom.minidom.parseString(plan.encode()).getElementsByTagName('HTTPSamplerProxy')) == 1


def test_the_jmeter_plan_carries_every_case_with_its_method_headers_and_body():
    import xml.dom.minidom
    plan = perf.jmeter_plan([{'name': 'puntos <socio>', 'path': '/member/points/1?x=1&y=2', 'headers': {'Accept': 'application/json'}},
                             {'name': 'soap', 'method': 'POST', 'path': '/cxf/x', 'body': '<a>&amp;</a>', 'contentType': 'text/xml'}], 15, 120)
    doc = xml.dom.minidom.parseString(plan.encode())
    samplers = doc.getElementsByTagName('HTTPSamplerProxy')
    assert [s.getAttribute('testname') for s in samplers] == ['puntos <socio>', 'soap']
    text = {p.getAttribute('name'): p.firstChild.data if p.firstChild else '' for p in doc.getElementsByTagName('stringProp')}
    assert '${__P(USERS,15)}' in plan and '${__P(SECONDS,120)}' in plan and '${__P(HOST,localhost)}' in plan
    assert '/member/points/1?x=1&y=2' in [p.firstChild.data for p in doc.getElementsByTagName('stringProp') if p.getAttribute('name') == 'HTTPSampler.path']
    assert any(p.firstChild and p.firstChild.data == '<a>&amp;</a>' for p in doc.getElementsByTagName('stringProp') if p.getAttribute('name') == 'Argument.value')
    assert text['HTTPSampler.method'] in ('GET', 'POST')


def test_the_performance_result_is_recorded_with_the_migration():
    import types
    import uuid as _uuid
    from mf.worker.tasks import _register_responses
    wm = types.SimpleNamespace(summary={'target': 'tomcat-war', 'equivalence': {'routeStructure': {'status': 'PASS'}}})
    run = types.SimpleNamespace(id=_uuid.uuid4(), workspace_migration_id=_uuid.uuid4())
    perf_summary = {'users': 10, 'seconds': 60, 'cases': 150, 'casesTotal': 201, 'comparison': {'status': 'PASS', 'tolerance': 0.2, 'reasons': []}}
    _register_responses(types.SimpleNamespace(get=lambda m, k: wm), run, {
        'status': 'PASS', 'mode': 'HUMO', 'totals': {'cases': 1, 'same': 1}, 'performance': perf_summary}, 'ev')
    assert wm.summary['performance']['comparison']['status'] == 'PASS' and wm.summary['performance']['httpTestRunId'] == str(run.id)


def test_an_error_under_load_is_an_answer_other_than_the_one_given_alone(tmp_path):
    """Posadas smoke test: many services answer 500 on BOTH sides by design (no data, simulated systems); the migrated side
    was faster, so its share of those 500s grew and a 5xx count said FAIL. Only a different answer than alone (or none) counts."""
    fast_500 = server(0.002, status=500)
    try:
        r = perf.run({'url': fast_500[1]}, {'url': fast_500[1]}, CASES, tmp_path, 2, 1, 0, 0.2, 'HUMO')
    finally:
        fast_500[0].shutdown()
    assert r['original']['errors'] == 0 and r['migrated']['errors'] == 0 and r['comparison']['status'] != 'FAIL'
    assert r['original']['statuses'] == {'500': r['original']['requests']}       # the 500s are still reported, as statuses

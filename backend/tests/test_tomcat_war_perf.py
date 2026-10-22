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

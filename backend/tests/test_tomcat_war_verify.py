"""Equivalence evidence of the tomcat-war profile (mf.tomcat_war.verify). The JDK/Maven part (compiling the original
against Camel 2 and dumping the routes) is exercised with the pilot repository; here: comparison, module graph, the
baseline flavour of the Blueprint conversion and the HTTP differential test against two local servers."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import ROOT
from mf import cli_tool
from mf.tomcat_war import blueprint, pipeline, verify

FIXTURE = ROOT / 'examples' / 'fuse-blueprint'


# --- route structure ---------------------------------------------------------------------------------------------------
def dump(tmp_path, name, text):
    f = tmp_path / name
    f.write_text(text, encoding='utf-8')
    return verify.read_dump(f, verify.load_equivalences())


def test_intended_changes_and_print_differences_are_not_differences(tmp_path):
    old, _ = dump(tmp_path, 'c2.txt', 'ROUTE R | direct:a\n'
                                      '  setHeader {simple:${property.x}} name=h\n'
                                      '  filter {(XPath: //status = 1) and (header{e} is not null)}\n'
                                      '  to uri=sql:{{sql.q}}?outputType=SelectOne\n'
                                      '  onException exceptions=org.apache.camel.processor.validation.PredicateValidationException\n'
                                      'ROUTE RestConfig | rest:get:/healthz\n'
                                      '  setBody {constant:OK}\n')
    new, _ = dump(tmp_path, 'c4.txt', 'ROUTE R | direct:a\n'
                                      '  setHeader {simple:${exchangeProperty.x}} name=h\n'
                                      '  filter {(xpath{//status = 1}) and (header{e} is not null)}\n'
                                      '  to uri=sql:@sql.q@?outputType=SelectOne\n'
                                      '  onException exceptions=org.apache.camel.support.processor.PredicateValidationException\n'
                                      'ROUTE RestConfig | rest:get:/healthz\n'
                                      '  to uri=language:constant:OK\n')
    res = verify.compare_dumps(old, new)
    assert len(res['same']) == 2 and not res['different'] and not res['onlyOriginal'] and not res['onlyMigrated']


def test_a_node_that_moved_under_another_parent_is_a_difference(tmp_path):
    # the silent Camel 4 change: same steps, but the last one left the otherwise() block
    old, _ = dump(tmp_path, 'c2.txt', 'ROUTE R | direct:a\n  choice\n    when {x}\n      to uri=direct:err\n    otherwise\n      to uri=direct:ok\n      to uri=direct:response\n')
    new, _ = dump(tmp_path, 'c4.txt', 'ROUTE R | direct:a\n  choice\n    when {x}\n      to uri=direct:err\n    otherwise\n      to uri=direct:ok\n  to uri=direct:response\n')
    diff = verify.compare_dumps(old, new)['different']
    assert [(x['side'], x['depth'], x['node']) for x in diff[0]['lines']] == [('original', 3, 'to uri=direct:response'), ('migrado', 1, 'to uri=direct:response')]


def test_fix_route_keeps_only_the_end_removal_that_restores_the_camel2_tree(tmp_path, monkeypatch):
    """PointsRoute (member-santander): Camel 2 kept `.to(direct:restResponse)` inside the otherwise branch; Camel 4 moves it out.
    Each .end() removal is compiled and dumped (faked here by counting the .end() left); only the one that yields the Camel 2
    tree is accepted, and nothing is accepted when no removal does."""
    src = tmp_path / 'PointsRoute.java'
    src.write_text('class PointsRoute { void configure() {\n  from("direct:p")\n    .choice().when(x).log("a")\n    .otherwise()'
                   '\n      .filter(y).setBody(z)\n      .end()\n    .end()\n    .to("direct:restResponse");\n}}\n', encoding='latin-1')
    base = tmp_path / 'classes'
    base.mkdir()
    monkeypatch.setattr(verify, '_compile', lambda sources, cp, out, *a, **k: (out / 'compiled.txt').write_text(sources[0].read_text('latin-1')) or True)

    def run(cmd, log, **k):  # second .end() removed -> restResponse stays in the otherwise branch (depth 3), as Camel 2 built it
        code = (cmd[2] / 'compiled.txt').read_text()
        depth = 3 if code.count('.end()') == 1 and '      .end()' in code else 1
        cmd[3].write_text(f'ROUTE PointsRoute | direct:p\n{"  " * depth}to uri=direct:restResponse\n')
    monkeypatch.setattr(verify, '_run', run)
    camel2 = {'PointsRoute | direct:p': ['      to uri=direct:restResponse']}
    args = (tmp_path / 'trial', [], [], [], tmp_path, base)
    done = verify.fix_route(src, 'PointsRoute', 'direct:p', camel2, *args)
    assert done['removed'] == '.end()' and done['line'] == 7
    assert done['text'].count('.end()') == 1 and '      .end()\n    \n    .to("direct:restResponse")' in done['text']
    assert verify.fix_route(src, 'PointsRoute', 'direct:p', {'PointsRoute | direct:p': ['    to uri=x']}, *args) is None
    assert verify.fix_route(src, 'PointsRoute', 'direct:other', camel2, *args) is None


def test_missing_and_extra_routes_and_dump_errors(tmp_path):
    old, errs = dump(tmp_path, 'c2.txt', 'ROUTE A | direct:a\n  to uri=mock:a\nROUTE A | direct:gone\n  to uri=mock:b\nERROR Broken : NullPointerException: x\n')
    new, _ = dump(tmp_path, 'c4.txt', 'ROUTE A | direct:a\n  to uri=mock:a\nROUTE A | direct:new\n  to uri=mock:c\n')
    res = verify.compare_dumps(old, new)
    assert res['onlyOriginal'] == ['A | direct:gone'] and res['onlyMigrated'] == ['A | direct:new'] and errs == ['Broken : NullPointerException: x']


def test_module_graph_from_imports_and_class_names_in_strings(tmp_path):
    def module(name, files):
        d = tmp_path / name
        for f, text in files.items():
            (d / f).parent.mkdir(parents=True, exist_ok=True)
            (d / f).write_text(text, encoding='utf-8')
        return d
    src = {'util': module('util', {'a/util/X.java': 'package a.util;\npublic class X {}\n'}),
           'api': module('api', {'a/api/R.java': 'package a.api;\nimport a.util.X;\nclass R {}\n'}),
           'late': module('late', {'a/late/L.java': 'package a.late;\nclass L { Object c = Class.forName("a.util.X"); }\n'}),
           'alone': module('alone', {'a/alone/Z.java': 'package a.alone;\nclass Z { String s = "a.utilities"; }\n'})}
    deps = verify.module_dependencies(src)
    assert deps == {'util': [], 'api': ['util'], 'late': ['util'], 'alone': []}
    order = verify._order(deps)
    assert order.index('util') < order.index('api') and order.index('util') < order.index('late')


# --- baseline ----------------------------------------------------------------------------------------------------------
def test_baseline_conversion_changes_only_the_wiring():
    """The original code must run on Camel 2 exactly as written: xmljson stays a Camel data format, no Camel 4 renames."""
    ctx = (FIXTURE / 'orders-api/src/main/resources/OSGI-INF/blueprint/camel-context.xml').read_text(encoding='utf-8')
    out = blueprint.convert(ctx, 'camel-context.xml', [], 'org.migrationfactory.compat', camel2=True)
    assert '<xmljson id="xmljson"' in out and '<dataFormats>' in out and 'XmlJsonDataFormat' not in out
    assert 'xmlns="http://camel.apache.org/schema/spring"' in out and out.rstrip().endswith('</beans>')
    src = ('<blueprint xmlns="http://www.osgi.org/xmlns/blueprint/v1.0.0" xmlns:cxf="http://camel.apache.org/schema/blueprint/cxf">'
           '<cxf:cxfEndpoint id="e" address="/x"/><reference id="r" interface="a.B"/>'
           '<camelContext xmlns="http://camel.apache.org/schema/blueprint"><route><from uri="quartz2://t"/><setHeader headerName="h"><constant>1</constant></setHeader></route></camelContext></blueprint>')
    out = blueprint.convert(src, 'x.xml', [], 'org.migrationfactory.compat', camel2=True)
    assert 'xmlns:cxf="http://camel.apache.org/schema/cxf"' in out and 'cxf/jaxws' not in out
    assert 'quartz2://t' in out and 'headerName="h"' in out and 'osgi.ServiceImport' in out


def test_camel2_version_comes_from_the_original_poms():
    assert verify.camel2_version(pipeline.discover(FIXTURE)) == '2.23.2'


# --- responses ---------------------------------------------------------------------------------------------------------
def server(behaviour):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            status, ctype, body = behaviour(self.path, self.headers)
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):
            pass
    s = ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f'http://127.0.0.1:{s.server_address[1]}'


@pytest.fixture
def two_servers():
    def old(path, headers):
        if path == '/boom':
            return 500, 'text/plain', 'java.lang.IllegalStateException: sin datos. Exchange[ID-1-2-3]\n\tat org.apache.camel.Old.run(Old.java:10)\n'
        if path == '/missing':
            return 500, 'text/plain', 'error'
        return 200, 'application/json', '{"points": 801}'

    def new(path, headers):
        if path == '/boom':  # same error, other library frames and exchange id
            return 500, 'text/plain', 'java.lang.IllegalStateException: sin datos. Exchange[ID-9-9-9]\n\tat org.apache.camel.New.run(New.java:77)\n'
        if path == '/missing':
            return 200, 'application/json', '{"msg": "Not found"}'
        return 200, 'application/json', '{"points": 801}'
    a, ua = server(old)
    b, ub = server(new)
    yield ua, ub
    a.shutdown()
    b.shutdown()


def write_cases(tmp_path, cases):
    f = tmp_path / 'cases.json'
    f.write_text(json.dumps({'cases': cases}), encoding='utf-8')
    return f


def test_same_request_to_both_systems(tmp_path, two_servers):
    old, new = two_servers
    cases = [{'name': 'puntos', 'path': '/points/1'}, {'name': 'error', 'path': '/boom'}, {'name': 'sin resultado', 'path': '/missing'}]
    res = verify.responses(old, new, write_cases(tmp_path, cases), tmp_path / 'out')
    assert [c['state'] for c in res['cases']] == ['SAME', 'SAME', 'DIFFERENT'] and res['status'] == 'FAIL'
    assert res['cases'][2]['issues'] == ['HTTP 500 -> 200', "Content-Type 'text/plain' -> 'application/json'", 'cuerpo distinto']
    assert 'MIGRADO:  HTTP 200' in (tmp_path / 'out/reports/responses.md').read_text(encoding='utf-8')
    # a reviewed difference is recorded with its reason, never hidden
    cases[2]['accept'] = 'Acordado con el cliente: 200 con mensaje es el comportamiento correcto'
    res = verify.responses(old, new, write_cases(tmp_path, cases), tmp_path / 'out')
    assert res['status'] == 'PASS_WITH_ACCEPTED' and res['totals'] == {'cases': 3, 'same': 2, 'different': 0, 'accepted': 1, 'notExecutable': 0}
    assert 'Acordado con el cliente' in (tmp_path / 'out/reports/responses.md').read_text(encoding='utf-8')


def test_unreachable_system_is_not_a_pass(tmp_path, two_servers):
    old, _ = two_servers
    res = verify.responses(old, 'http://127.0.0.1:1', write_cases(tmp_path, [{'name': 'x', 'path': '/a'}]), tmp_path / 'out', timeout=3)
    assert res['status'] == 'NOT_EXECUTABLE' and res['cases'][0]['state'] == 'NOT_EXECUTABLE'


def test_g4_evidence_needs_both_checks(tmp_path, two_servers):
    old, new = two_servers
    verify.responses(old, new, write_cases(tmp_path, [{'name': 'puntos', 'path': '/points/1'}]), tmp_path / 'out')
    ev = json.loads((tmp_path / 'out/reports/equivalence-evidence.json').read_text(encoding='utf-8'))
    assert ev['gate'] == 'G4' and ev['checks']['responses']['status'] == 'PASS' and ev['missing'] == ['route-structure'] and ev['status'] == 'REVIEW'
    verify._save(tmp_path / 'out', 'route-structure', {'status': 'PASS', 'totals': {'identical': 3}}, '# ok\n')
    assert json.loads((tmp_path / 'out/reports/equivalence-evidence.json').read_text(encoding='utf-8'))['status'] == 'PASS'
    verify._save(tmp_path / 'out', 'route-structure', {'status': 'FAIL', 'totals': {'identical': 2, 'unexpected': 1}}, '# x\n')
    assert json.loads((tmp_path / 'out/reports/equivalence-evidence.json').read_text(encoding='utf-8'))['status'] == 'FAIL'


def test_cli_verify_responses_exit_codes(tmp_path, two_servers, capsys):
    old, new = two_servers
    ok = write_cases(tmp_path, [{'name': 'puntos', 'path': '/points/1'}])
    assert cli_tool.main(['verify', 'responses', '--old', old, '--new', new, '--cases', str(ok), '--output', str(tmp_path / 'o1')]) == cli_tool.EXIT_OK
    bad = write_cases(tmp_path, [{'name': 'sin resultado', 'path': '/missing'}])
    assert cli_tool.main(['verify', 'responses', '--old', old, '--new', new, '--cases', str(bad), '--output', str(tmp_path / 'o2')]) == cli_tool.EXIT_CRITICAL
    assert 'DIFFERENT' in capsys.readouterr().out


def test_error_text_differences_are_told_apart_from_data_differences(tmp_path, two_servers):
    old, new = two_servers
    a, b = server(lambda p, h: (400, 'text/plain', 'Validation failed for Predicate[header{x}]')), server(lambda p, h: (400, 'text/plain', 'Validation failed for Predicate[x]'))
    try:
        res = verify.responses(a[1], b[1], write_cases(tmp_path, [{'name': 'validacion', 'path': '/v'}]), tmp_path / 'o1')
    finally:
        a[0].shutdown()
        b[0].shutdown()
    assert res['status'] == 'FAIL' and res['cases'][0]['kind'] == 'ERROR_TEXT' and res['differencesByKind'] == {'ERROR_TEXT': 1}
    res = verify.responses(old, new, write_cases(tmp_path, [{'name': 'sin resultado', 'path': '/missing'}, {'name': 'puntos', 'path': '/points/1'}]), tmp_path / 'o2')
    assert [c['kind'] for c in res['cases']] == ['DATA', None]


# --- evidence report ---------------------------------------------------------------------------------------------------
def test_report_shows_both_answers_side_by_side(tmp_path, two_servers):
    from mf.tomcat_war import evidence
    old, new = two_servers
    cases = [{'name': 'puntos', 'path': '/points/1', 'headers': {'Authorization': 'Bearer secreto', 'memberNumber': '1'}}, {'name': 'sin <resultado>', 'path': '/missing'}]
    verify.responses(old, new, write_cases(tmp_path, cases), tmp_path / 'out')
    res = evidence.report(tmp_path / 'out', screenshots=False, labels=('JBOSS', 'TOMCAT'))
    target = tmp_path / 'out/reports/evidence'
    index = (target / 'index.html').read_text(encoding='utf-8')
    assert res['pages'] == 3 and res['screenshots'] == 0 and res['problem'] is None
    assert 'caso-001.html' in index and 'sin &lt;resultado&gt;' in index and 'No se ha ejecutado' in index
    same, diff = (target / 'caso-001.html').read_text(encoding='utf-8'), (target / 'caso-002.html').read_text(encoding='utf-8')
    assert 'JBOSS' in same and 'TOMCAT' in same and '&quot;points&quot;: 801' in same and 'class="d"' not in same
    assert 'memberNumber: 1' in same and 'secreto' not in same  # credentials of the request never reach the report
    assert 'HTTP <b>500</b>' in diff and 'HTTP <b>200</b>' in diff and 'class="d"' in diff and 'Not found' in diff


def test_report_needs_results_and_says_when_there_is_no_browser(tmp_path, two_servers):
    from mf.tomcat_war import evidence
    with pytest.raises(verify.ToolError, match='verify responses'):
        evidence.report(tmp_path / 'vacio')
    old, new = two_servers
    verify.responses(old, new, write_cases(tmp_path, [{'name': 'puntos', 'path': '/points/1'}]), tmp_path / 'out')
    res = evidence.report(tmp_path / 'out', browser=str(tmp_path / 'no-existe.exe'))
    assert res['screenshots'] == 0 and 'navegador' not in (res['problem'] or '') and '--browser' in res['problem']
    assert (tmp_path / 'out/reports/evidence/index.html').exists()


def test_sql_component_keeps_the_camel2_result_only_in_the_migrated_code():
    src = ('<blueprint xmlns="http://www.osgi.org/xmlns/blueprint/v1.0.0"><bean id="sql" class="org.apache.camel.component.sql.SqlComponent">'
           '<property name="dataSource" ref="ds"/></bean></blueprint>')
    report = []
    assert 'class="org.migrationfactory.compat.sql.LegacySqlComponent"' in blueprint.convert(src, 'beans.xml', report, 'org.migrationfactory.compat')
    assert any('LegacySqlComponent' in x for x in report)
    assert 'class="org.apache.camel.component.sql.SqlComponent"' in blueprint.convert(src, 'beans.xml', [], 'org.migrationfactory.compat', camel2=True)


def test_routes_refuses_a_workspace_that_was_not_built(tmp_path):
    res = pipeline.migrate(FIXTURE, tmp_path / 'out')
    with pytest.raises(verify.ToolError, match='no está compilado'):
        verify.routes(FIXTURE, res['output'], tmp_path / 'out')

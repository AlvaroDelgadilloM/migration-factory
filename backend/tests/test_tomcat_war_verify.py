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


def test_routes_refuses_a_workspace_that_was_not_built(tmp_path):
    res = pipeline.migrate(FIXTURE, tmp_path / 'out')
    with pytest.raises(verify.ToolError, match='no está compilado'):
        verify.routes(FIXTURE, res['output'], tmp_path / 'out')

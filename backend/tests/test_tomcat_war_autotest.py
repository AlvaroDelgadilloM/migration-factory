"""Automatic test of a tomcat-war migration (mf.tomcat_war.autotest): the parts that decide what is sent and with which
configuration. Starting both servers needs a JDK, Maven and a real workspace; it is exercised with the pilot (docs/19)."""
import io
import json
import zipfile

import pytest

from mf.tomcat_war import autotest


def test_values_that_could_reach_a_real_system_are_never_kept(tmp_path):
    mock, jdbc = 'http://127.0.0.1:1/', 'jdbc:h2:file:x'
    keep = {'sql.select.member': 'select * from s_contact where x = ?', 'retries': '3', 'program': 'Fiesta Rewards', 'flag': 'true'}
    for k, v in keep.items():
        assert autotest.safe(k, v, mock, jdbc, tmp_path) == (v, False), k
    replaced = {'ws.siebel.url': 'https://siebel.cliente.com/eai', 'db.url': 'jdbc:oracle:thin:@10.1.2.3:1521/PROD',
                'broker': 'failover:(tcp://10.0.0.5:61616)', 'smtp': 'smtp://mail.cliente.com', 'host': 'siebel.cliente.com',
                'ip': '10.20.30.40:8080', 'mail.to': 'operaciones@cliente.com', 'ws.password': 'S3cr3t', 'api.user': 'admin',
                'path.files': '/opt/cliente/archivos'}
    for k, v in replaced.items():
        value, changed = autotest.safe(k, v, mock, jdbc, tmp_path)
        assert changed and v not in value and 'cliente' not in value and '10.' not in value, (k, value)


def test_missing_values_follow_what_the_code_does_with_them(tmp_path):
    code = ('from("sql:{{sql.q}}?delay={{scheduler.delay}}&cron=%[job.cron]").to("http4:{{ws.drools.url}}");\n'
            '@PropertyInject("token.ttl") private Integer ttl;\n'
            '<ext:property-placeholder placeholder-prefix="%[" placeholder-suffix="]"/>\n'
            '<bean id="h" class="a.Header"><property name="lunes" value="%[dia.lunes]"/></bean>')
    classes = {'a.Header': tmp_path / 'Header.java'}
    classes['a.Header'].write_text('class Header { public void setLunes(boolean v) {} }', encoding='latin-1')
    d = autotest.delimiters(code)
    assert ('%[', ']') in d and autotest.used_keys(code, d) >= {'sql.q', 'scheduler.delay', 'job.cron', 'ws.drools.url', 'token.ttl', 'dia.lunes'}
    value = lambda k: autotest.dummy(k, 'http://127.0.0.1:1', 'jdbc:h2:x', tmp_path, autotest.hint(k, code, d, classes))
    assert value('scheduler.delay') == '30000' and value('job.cron') == '0+0+3+1+1+?' and value('token.ttl') == '3'
    assert value('dia.lunes') == 'false' and value('sql.q') == 'select 1 from dual'
    assert autotest._scheme_in_code('ws.drools.url', code, d) and not autotest._scheme_in_code('sql.q', code, d)


def test_rest_cases_use_the_servlet_alias_and_parameter_values(tmp_path):
    project = tmp_path / 'm'
    (project / 'src/main/java').mkdir(parents=True)
    (project / 'src/main/java/R.java').write_text('restConfiguration().endpointProperty("servletName", "MemberServlet");', encoding='utf-8')
    dumps = {'m': {'RestConfig | rest:get:/inquiry/points/{memberNumber}': [], 'RestConfig | rest:post:/admin:update/{id}': [],
                   'RestConfig | direct:x': []}}
    cases = autotest.rest_cases(dumps, {'m': project}, {'MemberServlet': '/member'}, {'memberNumber': '95715637'})
    assert [(c['method'], c['path']) for c in cases] == [('GET', '/member/inquiry/points/95715637'), ('POST', '/member/admin/update/1')]
    assert cases[1]['body'] == '{}' and cases[1]['contentType'] == 'application/json'


def test_soap_cases_come_from_the_published_endpoints_wsdl(tmp_path):
    mod = tmp_path / 'svc'
    (mod / 'META-INF/modules/svc').mkdir(parents=True)
    (mod / 'wsdl').mkdir()
    (mod / 'META-INF/modules/svc/beans.xml').write_text(
        '<cxf:cxfEndpoint id="a" address="/Filtro" wsdlURL="wsdl/f.wsdl"/>'
        '<cxf:cxfEndpoint id="cliente" address="%{ws.siebel}" wsdlURL="wsdl/f.wsdl"/>', encoding='utf-8')
    (mod / 'wsdl/f.wsdl').write_text(
        '<definitions xmlns="http://schemas.xmlsoap.org/wsdl/" xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/" xmlns:tns="urn:f" '
        'targetNamespace="urn:f"><message name="buscarIn"><part name="p" element="tns:buscar"/></message>'
        '<portType name="P"><operation name="buscar"><input message="tns:buscarIn"/></operation></portType>'
        '<binding name="B" type="tns:P"><operation name="buscar"><soap:operation soapAction=""/></operation></binding></definitions>',
        encoding='utf-8')
    cases = autotest.soap_cases({'svc': {'dir': mod}})
    assert len(cases) == 1 and cases[0]['path'] == '/cxf/Filtro' and cases[0]['headers'] == {'SOAPAction': '""'}
    assert '<ns:buscar xmlns:ns="urn:f"/>' in cases[0]['body']  # the client endpoint (address from config) is not a service


def zip_of(files: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        for name, text in files.items():
            z.writestr(name, text)
    return b.getvalue()


def test_data_package_layout_is_checked():
    ok = autotest.inspect_data_package(zip_of({'etc/com.x/a.properties': 'k=v', 'sql/01-tablas.sql': 'create table t(x int);',
                                               'mocks.json': json.dumps([{'key': 'ws.x', 'body': '<ok/>'}]), 'mocks/r.xml': '<r/>',
                                               'valores.json': '{"id": "1"}', 'casos.json': '{"cases": [{"name": "a", "path": "/a"}]}'}))
    assert ok == {'etc': ['com.x/a.properties'], 'sql': ['01-tablas.sql'], 'mocks': 1, 'values': 1, 'cases': 1}
    for bad in ({'../etc/x.properties': 'k=v'}, {'run.sh': 'rm -rf /'}, {'mocks.json': '{no es json'}):
        with pytest.raises(autotest.AutoTestError):
            autotest.inspect_data_package(zip_of(bad))
    with pytest.raises(autotest.AutoTestError, match='zip'):
        autotest.inspect_data_package(b'texto')


def test_simulator_answers_from_the_data_package_or_503():
    import urllib.request
    sim = autotest.Simulator([{'key': 'ws.siebel', 'method': 'POST', 'contains': 'buscar', 'status': 200, 'contentType': 'text/xml', 'body': '<ok/>'}])
    try:
        req = urllib.request.Request(f'{sim.base}/ws.siebel/eai', data=b'<buscar/>', method='POST')
        assert urllib.request.urlopen(req).read() == b'<ok/>'
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f'{sim.base}/ws.otro/x')
        assert e.value.code == 503 and sim.calls == ['ws.siebel', 'ws.otro']
    finally:
        sim.stop()

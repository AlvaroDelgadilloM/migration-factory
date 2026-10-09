"""Profile tomcat-war (docs/19_TOMCAT_WAR.md): Fuse/Karaf bundles -> one WAR. Maven is not run here; the real build
and the Tomcat start are exercised with the pilot repository (see ESTADO_IMPLEMENTACION.md)."""
import hashlib
import json
import re
import shutil
import zipfile

import pytest

from conftest import ROOT
from mf import cli_tool
from mf.tomcat_war import blueprint, pipeline

FIXTURE = ROOT / 'examples' / 'fuse-blueprint'
COMPAT = 'org.migrationfactory.compat'


def tree_hash(root):
    h = hashlib.sha256()
    for f in sorted(p for p in root.rglob('*') if p.is_file()):
        h.update(f.relative_to(root).as_posix().encode() + b'\0' + f.read_bytes())
    return h.hexdigest()


def run(tmp_path, config=None):
    cfg = None
    if config is not None:
        cfg = tmp_path / 'config.json'
        cfg.write_text(json.dumps(config), encoding='utf-8')
    return pipeline.migrate(FIXTURE, tmp_path / 'out', cfg), tmp_path / 'out' / 'migrated-workspace'


# --- rules ---------------------------------------------------------------------------------------------------------
def test_rules_load_and_every_camel_artifact_is_in_the_verified_bom():
    rules = pipeline.load_rules()
    camel = {r['dependency'] for r in rules['dependencyRules'] if r['dependency'].startswith('org.apache.camel:')}
    camel |= {d for d in rules['baseDependencies'] if d.startswith('org.apache.camel:')}
    assert camel and camel <= rules['_bom']
    assert len({r['id'] for r in rules['javaRules']}) == len(rules['javaRules'])
    assert all(r['level'] in ('AUTO', 'AUTO_TEST', 'REVIEW') for r in rules['javaRules'])


@pytest.mark.parametrize('before, after', [
    ('import org.apache.camel.processor.aggregate.AggregationStrategy;', 'import org.apache.camel.AggregationStrategy;'),
    ('.log("x ${property.count}")', '.log("x ${exchangeProperty.count}")'),
    ('simple("${header.a} == \'x\' and ${body} != null")', 'simple("${header.a} == \'x\' && ${body} != null")'),
    ('.simple("${exception.message")', '.simple("${exception.message}")'),
    ('.to("xslt:x.xsl?failOnNullBody=false &saxon=true")', '.to("xslt:x.xsl?failOnNullBody=false&saxon=true")'),
    ('.get("/h").route().setBody().constant("OK");', '.get("/h").to("language:constant:OK");'),
    ('.filter(xpath("//a = 1").booleanResult())', '.filter(xpath("//a = 1", Boolean.class))'),
    ('.setFaultBody(exchangeProperty("f"))', f'.process(new {COMPAT}.cxf.ThrowFaultProperty("f"))'),
    ('import javax.xml.bind.JAXBException;', 'import jakarta.xml.bind.JAXBException;'),
    ('import javax.sql.DataSource;', 'import javax.sql.DataSource;'),                       # Java SE: untouched
    ('.to("direct-vm:x").to("xslt:a.xsl?saxon=true").marshal("xmljson")', '.to("direct-vm:x").to("xslt:a.xsl?saxon=true").marshal("xmljson")'),
])
def test_java_rules_are_one_line_mechanical_changes(before, after):
    assert pipeline.apply_java_rules(before, pipeline.load_rules(), COMPAT, 'X.java', []) == after


def test_nested_end_choice_is_flagged_for_review():
    nested = 'from("direct:a").choice().when(x).choice().when(y).to("a").otherwise().to("b").endChoice().otherwise().to("c").end();\n'
    flat = 'from("direct:a").choice().when(x).to("a").endChoice().otherwise().to("c").end();\n'
    assert pipeline.nested_end_choice(nested) == 1 and pipeline.nested_end_choice(flat) == 0


# --- blueprint -----------------------------------------------------------------------------------------------------
def test_blueprint_wiring_becomes_spring_and_the_rest_is_kept():
    src = (FIXTURE / 'orders-api/src/main/resources/OSGI-INF/blueprint/beans.xml').read_text(encoding='utf-8')
    report = []
    out = blueprint.convert(src, 'beans.xml', report, COMPAT)
    assert out.startswith('<?xml') and '<beans xmlns="http://www.springframework.org/schema/beans"' in out and out.rstrip().endswith('</beans>')
    assert 'blueprint' not in out.lower() and '<reference' not in out
    assert out.count('BridgePropertyPlaceholderConfigurer') == 1                       # one bridge with every location
    assert out.count('<value>file:${mf.etc}/demo/') == 3                               # 2 in the bridge + 1 for the custom prefix
    assert '<property name="placeholderPrefix" value="%{" />' in out
    assert f'<bean id="normalizer" class="{COMPAT}.osgi.ServiceImport">' in out
    assert '<property name="componentName" value="auditStorePrimary" />' in out
    assert '<bean id="ordersRoute" class="demo.orders.OrdersRoute" />' in out and '<!-- servicios OSGi -->' in out   # untouched
    assert any('2 <reference> -> ServiceImport' in line for line in report)


def test_xmljson_and_camel_namespace():
    src = (FIXTURE / 'orders-api/src/main/resources/OSGI-INF/blueprint/camel-context.xml').read_text(encoding='utf-8')
    out = blueprint.convert(src, 'camel-context.xml', [], COMPAT)
    assert 'xmlns="http://camel.apache.org/schema/spring"' in out and 'dataFormats' not in out
    bean = re.search(r'<bean id="xmljson" class="[\w.]+XmlJsonDataFormat">(.*?)</bean>', out, re.S)[1]
    assert '<property name="skipNamespaces" value="true" />' in bean and '<property name="trimSpaces" value="true" />' in bean
    assert out.index('XmlJsonDataFormat') < out.index('<camelContext')


def test_camel_xml_dsl_and_cxf_prefix():
    src = ('<blueprint xmlns="http://www.osgi.org/xmlns/blueprint/v1.0.0" xmlns:camel-cxf="http://camel.apache.org/schema/blueprint/cxf">\n'
           '<camel-cxf:cxfEndpoint id="e" address="/x"><camel-cxf:outInterceptors><ref component-id="i"/></camel-cxf:outInterceptors></camel-cxf:cxfEndpoint>\n'
           '<camelContext xmlns="http://camel.apache.org/schema/blueprint"><route id="r"><description>Una\n ruta</description>\n'
           '<from id="_from1" uri="quartz2://t"/><setHeader headerName="h" id="_to1"><constant>1</constant></setHeader>'
           '<marshal id="_m" ref="df"/><to id="_to1" uri="direct:a"/></route></camelContext></blueprint>')
    out = blueprint.convert(src, 'x.xml', [], COMPAT)
    assert 'xmlns:camel-cxf="http://camel.apache.org/schema/cxf/jaxws"' in out and '<ref bean="i"/>' in out
    assert '<route id="r" description="Una ruta">' in out and '<description>' not in out
    assert 'uri="quartz://t"' in out and '<setHeader name="h" id="_to1">' in out
    assert '<marshal id="_m"><custom ref="df"/></marshal>' in out
    assert 'id="_to1_2" uri="direct:a"' in out                                           # duplicated editor id made unique


def test_unsupported_blueprint_is_reported_not_guessed():
    report = []
    blueprint.convert('<blueprint xmlns="http://www.osgi.org/xmlns/blueprint/v1.0.0"><bean class="a.B"><argument value="1"/></bean></blueprint>',
                      'x.xml', report, COMPAT)
    assert any('[MANUAL]' in line and 'no cubierta' in line for line in report)


# --- pipeline ------------------------------------------------------------------------------------------------------
def test_workspace_becomes_one_war_project_and_the_source_is_not_touched(tmp_path):
    before = tree_hash(FIXTURE)
    res, ws = run(tmp_path, {'groupId': 'demo.migrated', 'version': '2.0.0'})
    assert tree_hash(FIXTURE) == before
    assert res['totals']['modules'] == 3 and res['totals']['xmlFiles'] == 4
    assert {p.name for p in ws.iterdir()} == {'pom.xml', 'compat', 'modules', 'webapp'}
    parent = (ws / 'pom.xml').read_text(encoding='utf-8')
    assert '<groupId>demo.migrated</groupId>' in parent and '<camel.version>4.18.4</camel.version>' in parent and '@' not in parent
    assert not list(ws.rglob('OSGI-INF'))                                                 # no Blueprint left
    assert (ws / 'compat/src/main/java/org/migrationfactory/compat/directvm/DirectVmComponent.java').exists()
    # orders-api: converted XML, rewritten Java, jar packaging, Camel 4 dependencies from the verified BOM
    mod = ws / 'modules/orders-api'
    java = (mod / 'src/main/java/demo/orders/OrdersRoute.java').read_text(encoding='utf-8')
    assert 'org.apache.camel.support.processor.PredicateValidationException' in java and '${exchangeProperty.requested}' in java
    assert '.to("language:constant:OK")' in java and 'apiContextListing' not in java and "'application/json' && ${body}" in java
    assert '.to("direct-vm:normalize")' in java and '.to("xslt:xsl/order.xsl?saxon=true")' in java and '.marshal("xmljson")' in java   # kept as written
    pom = (mod / 'pom.xml').read_text(encoding='utf-8')
    assert '<packaging>jar</packaging>' in pom and 'maven-bundle-plugin' not in pom and 'camel-xmljson' not in pom
    assert all(a in pom for a in ('camel-spring-xml', 'camel-servlet', 'mf-camel4-compat', 'commons-lang'))
    assert '<version>2.6</version>' in pom                                                # version property of the original POM resolved
    assert (mod / 'src/main/resources/META-INF/modules/orders-api/beans.xml').exists()
    # header("orderId") and the XSLT parameter need the exact name; ${header.Accept} in simple is case-insensitive
    assert (mod / 'src/main/resources/META-INF/mf/http-headers.txt').read_text().split() == ['orderId']
    # servlets registered through the OSGi HttpService -> web fragment with the same name and alias
    frag = (ws / 'modules/web-servlets/src/main/resources/META-INF/web-fragment.xml').read_text(encoding='utf-8')
    assert '<servlet-name>OrdersCamelServlet</servlet-name>' in frag and '<url-pattern>/orders/*</url-pattern>' in frag
    agg = (ws / 'modules/migrated-modules-all/pom.xml').read_text(encoding='utf-8')
    assert all(f'<artifactId>{m}</artifactId>' in agg for m in ('common-services', 'orders-api', 'web-servlets'))


def test_services_nobody_exports_and_manual_points_make_the_result_partial(tmp_path):
    res, _ = run(tmp_path)
    assert res['status'] == 'PARTIAL_SUCCESS'
    assert res['unresolvedServices'] == [{'module': 'orders-api', 'bean': 'auditStore', 'interface': 'demo.audit.AuditStore', 'componentName': 'auditStorePrimary'}]
    md = (tmp_path / 'out/reports/tomcat-war-report.md').read_text(encoding='utf-8')
    assert 'demo.audit.AuditStore' in md and 'SIMPLE_UNCLOSED_BRACE' in md
    saved = json.loads((tmp_path / 'out/reports/tomcat-war-report.json').read_text(encoding='utf-8'))
    assert saved['profile'] == 'tomcat-war-camel-4.18' and saved['totals'] == res['totals']


def test_client_config_patches_containers_and_war_dependencies(tmp_path):
    cfg = {'containers': {'names': ['a', 'b'], 'file': 'ci.yml', 'skipPattern': 'SKIP_{NAME}: true'},
           'extraWarDependencies': ['com.h2database:h2:2.4.240'],
           'patches': [{'name': 'ruta de salud propia', 'module': 'orders-api', 'file': 'OrdersRoute.java',
                        'find': '.to("language:constant:OK")', 'replace': '.to("direct:health")'}]}
    res, ws = run(tmp_path, cfg)
    assert res['totals']['patches'] == 1
    assert '.to("direct:health")' in (ws / 'modules/orders-api/src/main/java/demo/orders/OrdersRoute.java').read_text(encoding='utf-8')
    props = (ws / 'modules/orders-api/src/main/resources/META-INF/modules/orders-api/module.properties').read_text()
    assert 'containers=a,b' in props                                                      # no CI file: deployed everywhere
    assert '<artifactId>h2</artifactId>' in (ws / 'webapp/pom.xml').read_text(encoding='utf-8')


def test_a_patch_that_no_longer_matches_aborts(tmp_path):
    cfg = {'patches': [{'name': 'obsoleto', 'module': 'orders-api', 'file': 'OrdersRoute.java', 'find': 'texto que ya no existe', 'replace': 'x'}]}
    with pytest.raises(pipeline.PatchMismatch):
        run(tmp_path, cfg)
    cfg['patches'][0] |= {'file': 'NoExiste.java'}
    with pytest.raises(pipeline.PatchMismatch):
        run(tmp_path, cfg)


def test_camel_artifact_outside_the_bom_is_refused(tmp_path, monkeypatch):
    rules = dict(pipeline.load_rules())
    rules['baseDependencies'] = rules['baseDependencies'] + ['org.apache.camel:camel-made-up']
    monkeypatch.setattr(pipeline, 'load_rules', lambda: rules)
    with pytest.raises(pipeline.InvalidInput, match='camel-made-up'):
        run(tmp_path)


def test_output_inside_the_source_is_refused(tmp_path):
    src = tmp_path / 'src'
    shutil.copytree(FIXTURE, src)
    with pytest.raises(pipeline.InvalidInput):
        pipeline.migrate(src, src / 'out')


# --- CLI -----------------------------------------------------------------------------------------------------------
def test_cli_migrate_and_plan(tmp_path, capsys):
    out = tmp_path / 'out'
    assert cli_tool.main(['migrate', str(FIXTURE), '--target', 'tomcat-war', '--output', str(out)]) == cli_tool.EXIT_OK
    assert 'PARTIAL_SUCCESS' in capsys.readouterr().out
    with zipfile.ZipFile(out / 'migrated-workspace.zip') as z:
        names = z.namelist()
    assert 'pom.xml' in names and 'webapp/src/main/webapp/WEB-INF/web.xml' in names
    assert cli_tool.main(['migrate', str(FIXTURE), '--target', 'tomcat-war', '--output', str(out), '--fail-on-critical']) == cli_tool.EXIT_CRITICAL
    plan = tmp_path / 'plan'
    assert cli_tool.main(['plan', str(FIXTURE), '--target', 'tomcat-war', '--output', str(plan)]) == cli_tool.EXIT_OK
    assert (plan / 'reports/tomcat-war-report.md').exists() and not (plan / 'migrated-workspace').exists()

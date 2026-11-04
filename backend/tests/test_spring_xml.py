"""Spring XML context of a JBoss WAR -> Spring Boot (mf.spring_xml), on the examples' own XML."""
import re
import shutil
from pathlib import Path

from mf import spring_xml

EX = Path(__file__).resolve().parents[2] / 'examples'
PEDIDOS = EX / 'jboss-pedidos-completo' / 'app'
SHOP = EX / 'jboss-legacy-shop' / 'app'
ROUTES = {'demo.pedidos.PedidosRoutes', 'demo.pedidos.FondoRoutes', 'demo.pedidos.ServiciosRoutes'}


def _damaged(xml: str) -> str:
    """What the Camel upgrade recipe leaves: <property> children of Spring beans wrapped in <properties>."""
    return re.sub(r'(<bean\b[^>]*[^/]>)(.*?)(\s*</bean>)', lambda m: f'{m[1]}\n<properties>{m[2]}\n</properties>{m[3]}', xml, flags=re.S)


def test_facts_from_web_xml():
    f = spring_xml.facts((PEDIDOS / 'src/main/webapp/WEB-INF/web.xml').read_text(encoding='utf-8'))
    assert f == {'contextConfigLocation': ['/WEB-INF/applicationContext.xml'], 'cxfServletPattern': '/ws/*'}


def test_migrate_pedidos_xml():
    xml = _damaged((PEDIDOS / 'src/main/webapp/WEB-INF/applicationContext.xml').read_text(encoding='utf-8'))
    assert '<properties>' in xml
    r = spring_xml.migrate_xml(xml, {'pedidosDS'}, ROUTES)
    out = r['xml']
    assert '<properties>' not in out and '<property name="brokerURL" value="${broker.url}"/>' in out
    assert 'jndi-lookup' not in out.replace('xmlns:jee', '').replace('spring-jee', '') and 'META-INF/cxf/cxf.xml' not in out
    assert 'xmlns:cxf="http://camel.apache.org/schema/cxf/jaxws"' in out and 'cxf/jaxws/camel-cxf.xsd' in out
    assert '<camelContext' not in out and 'demo.pedidos.PedidosRoutes' not in out and 'demo.pedidos.ValidadorPedido' in out
    assert '<cxf:cxfEndpoint id="buroCredito"' in out and 'http-conf:conduit' in out
    assert r['imports'] == ['classpath:pedidos.properties']
    assert r['routes'].startswith('<?xml') and '<rest path="/salud">' in r['routes'] and '<route id="salud">' in r['routes']
    assert '<setHeader name="Content-Type">' in r['routes'] and 'headerName' not in r['routes']
    assert '<routeBuilder' not in r['routes'] and '<propertyPlaceholder' not in r['routes']
    assert r['manual'] == [] and not r['empty']


def test_shop_xml_has_nothing_left():
    r = spring_xml.migrate_xml((SHOP / 'src/main/webapp/WEB-INF/applicationContext.xml').read_text(encoding='utf-8'),
                               {'shopDS'}, {'demo.shop.ShopRoutes'})
    assert r['empty'] and r['routes'] is None and r['manual'] == []


def test_unknown_content_is_reported():
    xml = ('<beans xmlns="http://www.springframework.org/schema/beans" xmlns:jee="x"><jee:jndi-lookup id="otro" jndi-name="java:/x"/>'
           '<camelContext id="c" trace="true" xmlns="http://camel.apache.org/schema/spring"><onException><exception>E</exception>'
           '</onException><route id="r"><from uri="direct:a"/><setProperty propertyName="p"><constant>1</constant></setProperty></route>'
           '</camelContext></beans>')
    r = spring_xml.migrate_xml(xml, set(), set())
    assert any('trace' in m for m in r['manual']) and any('onException' in m for m in r['manual']) and any('otro' in m for m in r['manual'])
    assert '<setProperty name="p">' in r['routes'] and 'onException' not in r['routes']


def test_apply_on_module(tmp_path):
    app = tmp_path / 'app'
    shutil.copytree(PEDIDOS / 'src', app / 'src')
    ctx = app / 'src/main/webapp/WEB-INF/applicationContext.xml'
    ctx.write_text(_damaged(ctx.read_text(encoding='utf-8')), encoding='utf-8')
    (app / 'pom.xml').write_text('<project><dependencyManagement><dependencies></dependencies></dependencyManagement><dependencies>\n'
                                 '<dependency><groupId>org.apache.activemq</groupId><artifactId>activemq-client</artifactId>'
                                 '<version>${activemq.version}</version></dependency>\n</dependencies></project>', encoding='utf-8')
    (app / 'src/main/webapp/WEB-INF/web.xml').unlink()  # WAR profile: gone before this step; facts come from the scan
    r = spring_xml.apply(tmp_path, {'op': 'spring-xml-context', 'file': 'app/src/main/webapp/WEB-INF/web.xml', 'prefix': 'app/',
                                    'locations': ['/WEB-INF/applicationContext.xml'], 'cxfServletPattern': '/ws/*',
                                    'package': 'demo.pedidos', 'dsBeans': ['pedidosDS'], 'routeBuilders': sorted(ROUTES)})
    assert r['applied'], r
    assert not ctx.exists() and (app / 'src/main/resources/legacy/applicationContext.xml').is_file()
    assert (app / 'src/main/resources/camel/applicationContext-routes.xml').is_file()
    cfg = (app / 'src/main/java/demo/pedidos/MigratedXmlContextConfig.java').read_text(encoding='utf-8')
    assert '@ImportResource({"classpath:legacy/applicationContext.xml"})' in cfg
    props = (app / 'src/main/resources/application.properties').read_text(encoding='utf-8')
    assert 'spring.config.import=classpath:pedidos.properties' in props and 'cxf.path=/ws' in props
    pom = (app / 'pom.xml').read_text(encoding='utf-8')
    assert '<artifactId>activemq-client</artifactId></dependency>' in pom and '<artifactId>camel-xml-io-dsl-starter</artifactId>' in pom
    assert pom.index('camel-xml-io-dsl-starter') > pom.index('</dependencyManagement>')

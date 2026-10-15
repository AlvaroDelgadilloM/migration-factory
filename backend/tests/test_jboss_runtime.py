"""JBoss WAR -> Spring Boot jar / Tomcat 10.1 WAR: what JBoss provided (JNDI datasource, driver, servlet mapping, context root) is
declared by the candidate. Verified end to end on examples/jboss-legacy-shop (evidence/legacy vs evidence/migrated*)."""
from pathlib import Path

from mf import cli_tool

SHOP = Path(__file__).resolve().parents[2] / 'examples/jboss-legacy-shop/app'
ACCEPT = {'CAMEL2_VERSION': 'ejemplo revisado contra la guía 2→3'}


def _runtime(war):
    report = cli_tool.analyze(SHOP, 'spring', None, False, lambda m: None)
    assert report['legacyRuntime']['jndiDataSources'][0]['bean'] == 'shopDS'
    plan = cli_tool.build_plan(report, 'spring', '21', ACCEPT, war)
    step = next(s for s in plan['steps'] if s['key'] == 'runtime-spring')
    return step['recipe']['yaml'], '\n'.join(step.get('notes', []))


def test_jboss_war_to_spring_boot_jar():
    y, _ = _runtime(False)
    assert 'packaging: "jar"' in y and '"spring-framework-bom"' in y and '"javax.servlet-api"' in y
    assert '"spring-boot-maven-plugin"' in y and '"spring-boot-starter-jdbc"' in y
    assert '"postgresql"' in y and '"42.2.27"' in y and 'scope: "runtime"' in y
    assert '/api/*' in y and 'context-path: \\"/shop\\"' in y and 'skip-binding-on-error-code: \\"true\\"' in y
    assert '${MF_DS_SHOPDS_URL}' in y and '${MF_DS_SHOPDS_PASSWORD}' in y                # env references only, never values
    assert 'registerAlias(\\"dataSource\\", \\"shopDS\\")' in y                      # routes keep #shopDS
    assert 'SpringBootServletInitializer' not in y and 'WEB-INF/web.xml"' not in y


def test_jboss_war_to_tomcat_war():
    y, notes = _runtime(True)
    assert 'ChangePackaging' not in y                                                   # already a war: stays a war
    assert 'SpringBootServletInitializer' in y and '"spring-boot-starter-tomcat"' in y and 'scope: "provided"' in y
    assert 'filePattern: "src/main/webapp/WEB-INF/web.xml"' in y                        # Boot registers the Camel servlet itself
    assert '${MF_DS_SHOPDS_URL}' in y and '/api/*' in y and 'Tomcat 10.1' in notes

"""Java 25: profile Spring Boot 4.1.1 + Camel 4.22.1 LTS (the first Camel line with official Java 25 support). Verified end to
end on examples/jboss-legacy-shop: Java 25 bytecode (major 69), Spring Boot 4.1.1, 9/9 cases equal to the legacy JBoss
(evidence/migrated-java25). Here: profile selection, the recipe, the JDK chosen from the POM and the API refusing Java 25 where
the stack does not support it."""
from pathlib import Path

import pytest

from helpers import scanned_project
from mf import cli_tool
from mf.profiles import java_versions, pick
from mf.worker import mavenops

SHOP = Path(__file__).resolve().parents[2] / 'examples/jboss-legacy-shop/app'
ACCEPT = {'CAMEL2_VERSION': 'ejemplo revisado contra la guía 2→3'}


def test_profiles_declare_their_java_versions():
    sb4 = pick('spring', key='spring-boot-4.1-camel-4.22')
    assert sb4['runtime_version'] == '4.1.1' and sb4['camel_version'] == '4.22.1' and java_versions(sb4) == ['21', '25']
    assert java_versions(pick('spring')) == ['17', '21'] and pick('spring')['camel_version'] == '4.14.9'   # default stays 3.5/4.14
    from mf import versions
    from types import SimpleNamespace
    t = versions.profile_target(SimpleNamespace(**{k: sb4[k] for k in ('key', 'version', 'name', 'runtime', 'runtime_version', 'camel_version',
                                                                       'dependency_mapping')}, id='x'))
    assert [j['version'] for j in t['java'] if j['validated']] == ['21', '25']
    assert java_versions(pick('quarkus')) == ['17', '21']                                                    # Camel Quarkus 3.40: 17/21


def test_java25_plan_upgrades_java_and_uses_boot4_starters():
    report = cli_tool.analyze(SHOP, 'spring', None, False, lambda m: None)
    plan = cli_tool.build_plan(report, 'spring', '25', ACCEPT, profile_key='spring-boot-4.1-camel-4.22')
    step = next(s for s in plan['steps'] if s['key'] == 'runtime-spring')
    y = step['recipe']['yaml'] + ' '.join(step['recipe']['activeRecipes'])
    assert 'org.openrewrite.java.migrate.UpgradeToJava25' in y and '"spring-boot-starter-webmvc"' in y
    assert 'camel-spring-boot-bom' in y and '"4.22.1"' in y and '"4.1.1"' in y
    with pytest.raises(cli_tool.InvalidInput, match='Java 17, 21'):                      # Camel 4.14 has no Java 25 support
        cli_tool.build_plan(report, 'spring', '25', ACCEPT)



def test_tomcat11_war_plan_on_spring_boot4():
    """Tomcat 11 WAR (Spring Boot 4.1 + Camel 4.22): verified end to end in tomcat:11.0.27-jdk25-temurin, 9/9 cases equal to the
    legacy JBoss (evidence/migrated-tomcat11). The plan keeps the war packaging, the container as provided and the initializer."""
    report = cli_tool.analyze(SHOP, 'spring', None, False, lambda m: None)
    plan = cli_tool.build_plan(report, 'spring', '25', ACCEPT, True, cli_tool.PROFILE_KEYS['tomcat11'])
    y = ' '.join(s['recipe'].get('yaml', '') + ' '.join(s['recipe'].get('activeRecipes', [])) for s in plan['steps'] if s.get('recipe'))
    assert 'UpgradeToJava25' in y and 'spring-boot-starter-tomcat' in y and '"provided"' in y and '"4.1.1"' in y
    assert 'WAR para Tomcat 11 (SpringBootServletInitializer)' in y and 'SpringBootServletInitializer' in y
    assert 'ChangePackaging' not in y                                                          # the JBoss WAR stays a WAR
    assert pick('spring', war=True)['key'] == 'tomcat-10.1-war-spring-boot-3.5-camel-4.14'     # default WAR target unchanged


def test_maven_uses_the_jdk_of_the_pom_release(tmp_path, monkeypatch):
    jdk = tmp_path / 'jdk-25'
    (jdk / 'bin').mkdir(parents=True)
    monkeypatch.setenv('MF_JDK25_HOME', str(jdk))
    from mf.config import get_settings
    get_settings.cache_clear()
    try:
        proj = tmp_path / 'p'
        proj.mkdir()
        (proj / 'pom.xml').write_text('<project><properties><maven.compiler.target>25</maven.compiler.target></properties></project>')
        assert mavenops.pom_release(proj) == '25' and mavenops.java_home_or_none(mavenops.pom_release(proj)) == str(jdk)
        (proj / 'pom.xml').write_text('<project><properties><maven.compiler.source>1.8</maven.compiler.source>'
                                      '<maven.compiler.target>1.8</maven.compiler.target></properties></project>')
        assert mavenops.pom_release(proj) == '8' and mavenops.java_home_or_none('8') is None   # the legacy baseline keeps the default JDK
    finally:
        get_settings.cache_clear()


def test_api_refuses_java25_on_a_profile_without_it(client, token, pilot_repo, monkeypatch):
    pid, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    profiles = client.get('/api/v1/profiles', headers=token('ana.arquitecta')).json()
    sb3 = next(p for p in profiles if p['key'] == 'spring-boot-3.5-camel-4.14')
    sb4 = next(p for p in profiles if p['key'] == 'spring-boot-4.1-camel-4.22')
    bad = client.post(f"/api/v1/scans/{scan['id']}/plans", headers=token('ana.arquitecta'), json={'profileId': sb3['id'], 'java': '25'})
    assert bad.status_code == 422 and bad.json()['code'] == 'VERSION_NOT_VALIDATED' and 'Camel 4.22' in bad.json()['message']
    ok = client.post(f"/api/v1/scans/{scan['id']}/plans", headers=token('ana.arquitecta'), json={'profileId': sb4['id'], 'java': '25'})
    assert ok.status_code == 201, ok.text
    assert any('UpgradeToJava25' in n for s in ok.json()['steps'] for n in s.get('notes', []))

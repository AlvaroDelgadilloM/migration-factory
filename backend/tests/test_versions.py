"""Java / Spring versions a user may choose (mf.versions, GET /target-versions): only validated combinations."""
import pytest

from mf import versions
from mf.schemas import WorkspaceMigrationIn

API = '/api/v1'


def test_catalog_offers_java_21_and_25_with_the_spring_line_of_each_target(client, token):
    cat = client.get(f'{API}/target-versions', headers=token('ana.arquitecta')).json()
    tomcat = next(t for t in cat if t['target'] == 'tomcat-war')
    assert [(j['version'], j['validated']) for j in tomcat['java']] == [('21', True), ('25', True)]
    assert tomcat['spring'][0]['label'].startswith('Spring Framework 6.2') and tomcat['spring'][0]['java'] == ['21', '25']
    profiles = [t for t in cat if t['target'] == 'profile']
    boot4 = [t for t in profiles if t['label'].startswith('Spring Boot 4.1')]  # Camel 4.22 LTS: Java 25 validated (tests/test_java25.py)
    tomcat11 = [t for t in profiles if t['label'].startswith('Tomcat 11 WAR')]   # same engine as a WAR: Java 25 validated too
    assert len(tomcat11) == 1 and [(j['version'], j['validated']) for j in tomcat11[0]['java']] == [('21', True), ('25', True)]
    assert len(boot4) == 1 and [(j['version'], j['validated']) for j in boot4[0]['java']] == [('21', True), ('25', True)]
    others = [t for t in profiles if t not in boot4 + tomcat11]
    assert others and all([(j['version'], j['validated']) for j in t['java']] == [('21', True), ('25', False)] for t in others)
    assert all(t['java'][1]['reason'] for t in others)  # a combination not validated says why


def test_a_combination_that_was_not_validated_is_refused():
    tomcat = versions.tomcat_war()
    versions.check(tomcat, '25', tomcat['spring'][0]['key'])
    with pytest.raises(ValueError, match='no se ofrece'):
        versions.check(tomcat, '17')
    with pytest.raises(ValueError, match='no existe'):
        versions.check(tomcat, '21', 'spring-7.0')
    assert WorkspaceMigrationIn(target='tomcat-war', java='25').java == '25'
    with pytest.raises(ValueError):
        WorkspaceMigrationIn(target='tomcat-war', java='17')


def test_plans_accept_only_the_validated_java_of_their_profile(client, token):
    auth = token('ana.arquitecta')
    profile = next(p for p in client.get(f'{API}/profiles', params={'status': 'validated'}, headers=auth).json()
                   if '25' not in p['dependencyMapping'].get('javaVersions', []))
    entry = next(t for t in client.get(f'{API}/target-versions', headers=auth).json() if t.get('profileId') == profile['id'])
    with pytest.raises(ValueError, match='Pendiente de validar'):
        versions.check({**entry, 'java': [{'version': j['version'], 'validated': j['validated'], 'reason': j['reason']} for j in entry['java']]}, '25')

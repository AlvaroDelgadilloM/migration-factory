"""API flows: projects, authorization/isolation, scans, findings review, plans, executions, artifacts, audit."""
import uuid

from helpers import FakeMaven, install, new_project, run_job, scanned_project

API = '/api/v1'


def find(client, token, scan_id, user='ana.arquitecta', **params):
    r = client.get(f'{API}/scans/{scan_id}/findings', headers=token(user), params=params)
    assert r.status_code == 200, r.text
    return r.json()['items']


def review(client, token, fid, user, decision, reason='Revisado con evidencia en el ticket MF-1', version=None):
    if version is None:
        version = client.get(f'{API}/findings/{fid}', headers=token(user)).headers['etag']
    return client.patch(f'{API}/findings/{fid}/review', headers=token(user) | {'If-Match': str(version)},
                        json={'decision': decision, 'reason': reason})


def test_requires_authentication(client):
    assert client.get(f'{API}/projects').status_code == 401
    assert client.get(f'{API}/projects', headers={'Authorization': 'Bearer nope'}).json()['code'] == 'UNAUTHENTICATED'
    assert client.post(f'{API}/auth/dev-login', json={'username': 'nobody'}).status_code == 404


def test_project_registration_validates_url(client, token):
    for url in ('http://github.com/a/b.git', 'https://u:p@github.com/a/b.git', 'https://intranet.local/a.git', 'file:///etc'):
        r = client.post(f'{API}/projects', headers=token('ana.arquitecta'), json={'name': 'Bad repo', 'repositoryUrl': url})
        assert r.status_code == 422 and r.json()['requestId'], r.text
    r = client.post(f'{API}/projects', headers=token('ana.arquitecta'),
                    json={'name': 'Bad cred', 'repositoryUrl': 'file:///nonexistent', 'credentialRef': 'ghp_literal'})
    assert r.status_code == 422


def test_project_isolation_and_roles(client, token, pilot_repo):
    pid = new_project(client, token, pilot_repo)
    other = new_project(client, token, pilot_repo, owner='mallory.externa', members={})
    # Non-members do not even see the project: 404, not 403.
    assert client.get(f'{API}/projects/{pid}', headers=token('mallory.externa')).status_code == 404
    assert client.get(f'{API}/projects/{other}', headers=token('diego.dev')).status_code == 404
    ids = {p['id'] for p in client.get(f'{API}/projects', headers=token('mallory.externa')).json()['items']}
    assert pid not in ids and other in ids
    # Members with insufficient role get 403 with the required roles.
    r = client.patch(f'{API}/projects/{pid}', headers=token('diego.dev') | {'If-Match': '1'}, json={'independentReview': False})
    assert r.status_code == 403 and r.json()['code'] == 'FORBIDDEN'
    assert client.post(f'{API}/projects/{pid}/scans', headers=token('aldo.auditor'), json={}).status_code == 403
    # Platform admin sees everything.
    assert client.get(f'{API}/projects/{other}', headers=token('admin')).status_code == 200
    perms = client.get(f'{API}/projects/{pid}/permissions', headers=token('aldo.auditor')).json()
    assert perms['role'] == 'auditor' and 'scan' not in perms['permissions'] and 'audit' in perms['permissions']


def test_project_update_requires_if_match(client, token, pilot_repo):
    pid = new_project(client, token, pilot_repo)
    r = client.patch(f'{API}/projects/{pid}', headers=token('ana.arquitecta') | {'If-Match': '99'}, json={'requiredGates': ['G2']})
    assert r.status_code == 409 and r.json()['code'] == 'VERSION_CONFLICT'
    r = client.patch(f'{API}/projects/{pid}', headers=token('ana.arquitecta') | {'If-Match': '1'}, json={'requiredGates': ['G9']})
    assert r.status_code == 422
    r = client.patch(f'{API}/projects/{pid}', headers=token('ana.arquitecta') | {'If-Match': '1'},
                     json={'prConfig': {'provider': 'github', 'repository': 'acme/orders', 'credentialRef': 'env:MF_CRED_GH'}})
    assert r.status_code == 200 and r.json()['version'] == 2


def test_scan_idempotency_and_conflicts(client, token, pilot_repo, monkeypatch):
    install(monkeypatch, FakeMaven())
    pid = new_project(client, token, pilot_repo)
    h = token('diego.dev') | {'Idempotency-Key': 'scan-1'}
    assert client.post(f'{API}/projects/{pid}/scans', headers=token('diego.dev'), json={'ref': 'main'}).json()['code'] == 'VALIDATION_ERROR'
    first = client.post(f'{API}/projects/{pid}/scans', headers=h, json={'target': 'spring'})
    again = client.post(f'{API}/projects/{pid}/scans', headers=h, json={'target': 'spring'})
    assert first.status_code == 202 and again.json() == first.json()
    assert client.post(f'{API}/projects/{pid}/scans', headers=h, json={'target': 'quarkus'}).json()['code'] == 'IDEMPOTENCY_CONFLICT'
    r = client.post(f'{API}/projects/{pid}/scans', headers=token('diego.dev'), json={})
    assert r.status_code == 409 and r.json()['code'] == 'SCAN_IN_PROGRESS'
    # cancel while queued: final immediately; cancelling again is a conflict
    job = first.json()['jobId']
    assert client.post(f'{API}/jobs/{job}/cancel', headers=token('diego.dev')).json()['state'] == 'cancelled'
    assert client.post(f'{API}/jobs/{job}/cancel', headers=token('diego.dev')).status_code == 409
    run_job(job)  # a late delivery of a cancelled job does nothing
    assert client.get(f"{API}/scans/{first.json()['scanId']}", headers=token('diego.dev')).json()['status'] == 'cancelled'


def test_scan_persists_inventory_with_provenance(client, token, pilot_repo, monkeypatch):
    pid, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    assert scan['commitSha'] is None and len(scan['snapshotHash']) == 64 and scan['projectRoot'] == '.'  # local folder: no Git
    assert scan['mavenResolution'] == 'maven-effective', scan['mavenResolutionDetail']
    assert scan['summary']['routesJavaApproximate'] == 3 and scan['summary']['routesXmlParsed'] == 1
    mods = client.get(f"{API}/scans/{scan['id']}/modules", headers=token('aldo.auditor')).json()
    routes_mod = next(m for m in mods if m['artifactId'] == 'orders-routes')
    core = next(d for d in routes_mod['dependencies'] if d['artifactId'] == 'camel-core')
    assert core['versionObserved'] is None and core['versionResolved'] == '2.24.3' and core['versionSource'] == 'maven-effective'
    routes = client.get(f"{API}/scans/{scan['id']}/routes", headers=token('aldo.auditor'), params={'dsl': 'xml'}).json()
    assert routes['items'][0]['routeId'] == 'shared-notify' and routes['items'][0]['detection'] == 'xml-parsed'
    arts = client.get(f'{API}/projects/{pid}/artifacts', headers=token('aldo.auditor'), params={'scanId': scan['id']}).json()['items']
    assert {a['name'] for a in arts} >= {'report.json', 'report.html', 'effective-pom.xml'}
    html = next(a for a in arts if a['name'] == 'report.html')
    r = client.get(f"{API}/artifacts/{html['id']}/download", headers=token('aldo.auditor'))
    assert r.status_code == 200 and 'attachment' in r.headers['content-disposition']
    assert client.get(f"{API}/artifacts/{html['id']}/download", headers=token('mallory.externa')).status_code == 404


def test_findings_filters_pagination_and_review(client, token, pilot_repo, monkeypatch):
    pid, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    all_f = find(client, token, scan['id'])
    assert all_f[0]['blocking'] and {f['ruleId'] for f in all_f} >= {'CAMEL2_VERSION', 'CAMEL_VM', 'JAKARTA_JMS', 'JMS', 'JNDI'}
    assert all(f['severity'] == 'high' for f in find(client, token, scan['id'], severity='high'))
    page = client.get(f"{API}/scans/{scan['id']}/findings", headers=token('ana.arquitecta'), params={'limit': 2}).json()
    assert len(page['items']) == 2 and page['nextCursor'] and page['total'] == len(all_f)
    page2 = client.get(f"{API}/scans/{scan['id']}/findings", headers=token('ana.arquitecta'), params={'limit': 2, 'cursor': page['nextCursor']}).json()
    assert {f['id'] for f in page2['items']}.isdisjoint({f['id'] for f in page['items']})
    assert client.get(f"{API}/scans/{scan['id']}/findings", headers=token('ana.arquitecta'), params={'severity': 'urgent'}).status_code == 422

    vm = find(client, token, scan['id'], rule='CAMEL_VM')[0]
    assert review(client, token, vm['id'], 'diego.dev', 'discarded').status_code == 403      # developer cannot discard
    assert review(client, token, vm['id'], 'aldo.auditor', 'proposed').status_code == 403    # auditor is read-only
    assert review(client, token, vm['id'], 'rosa.revisora', 'discarded', reason='corto').status_code == 422
    assert review(client, token, vm['id'], 'diego.dev', 'proposed', version=7).status_code == 409   # stale If-Match
    r = review(client, token, vm['id'], 'diego.dev', 'proposed')
    assert r.status_code == 200 and r.json()['status'] == 'proposed' and r.headers['etag'] == '2'
    assert review(client, token, vm['id'], 'ana.arquitecta', 'proposed').json()['code'] == 'INVALID_TRANSITION'
    assert review(client, token, vm['id'], 'rosa.revisora', 'discarded').json()['status'] == 'discarded'
    detail = client.get(f"{API}/findings/{vm['id']}", headers=token('aldo.auditor')).json()
    assert [x['decision'] for x in detail['reviews']] == ['proposed', 'discarded']
    audit = client.get(f'{API}/audit', headers=token('aldo.auditor'), params={'projectId': pid, 'action': 'finding.review'}).json()
    assert audit['total'] == 2 and audit['items'][0]['beforeHash'] != audit['items'][0]['afterHash']
    assert client.get(f'{API}/audit', headers=token('diego.dev'), params={'projectId': pid}).status_code == 403


def _plan(client, token, scan, runtime='spring'):
    prof = next(p for p in client.get(f'{API}/profiles', headers=token('ana.arquitecta')).json() if p['runtime'] == runtime)
    r = client.post(f"{API}/scans/{scan['id']}/plans", headers=token('ana.arquitecta'), json={'profileId': prof['id']})
    assert r.status_code == 201, r.text
    return r.json()


def test_plan_blocks_until_camel2_review(client, token, pilot_repo, monkeypatch):
    pid, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    plan = _plan(client, token, scan)
    steps = {s['key']: s for s in plan['steps']}
    assert steps['camel-2-to-3']['state'] == 'blocked' and steps['camel-3-to-4']['state'] == 'blocked'
    assert steps['manual-camel-vm']['classification'] == 'MANUAL' and steps['manual-camel-vm']['recipe'] is None
    assert 'onlyIfUsing' in steps['runtime-spring']['recipe']['yaml'] and 'Application.java' in steps['runtime-spring']['recipe']['yaml']
    assert all(s['state'] != 'ready' for s in plan['steps'] if s['kind'] == 'recipe')
    # nothing runnable yet -> explicit conflict listing blockers
    r = client.post(f"{API}/plans/{plan['id']}/preview", headers=token('diego.dev'))
    assert r.status_code == 409 and r.json()['code'] == 'NOTHING_RUNNABLE' and r.json()['details']['blocked']
    assert client.post(f"{API}/scans/{scan['id']}/plans", headers=token('diego.dev'), json={'profileId': plan['profileId']}).status_code == 403
    camel2 = find(client, token, scan['id'], rule='CAMEL2_VERSION')[0]
    assert review(client, token, camel2['id'], 'ana.arquitecta', 'resolved',
                  reason='Rutas usan solo endpoints string y ProducerTemplate; revisado contra guía 2→3').status_code == 200
    steps = {s['key']: s for s in client.get(f"{API}/plans/{plan['id']}", headers=token('diego.dev')).json()['steps']}
    assert steps['camel-2-to-3']['state'] == 'cleared'
    assert steps['camel-3-to-4']['state'] == 'ready' and steps['jakarta-jms']['state'] == 'ready' and steps['runtime-spring']['state'] == 'ready'
    assert steps['manual-camel-vm']['state'] == 'blocked'  # still blocks the PR, not the recipes
    # apply requires an approved plan; approval needs If-Match
    r = client.post(f"{API}/plans/{plan['id']}/executions", headers=token('diego.dev'), json={'mode': 'apply'})
    assert r.json()['code'] == 'PLAN_NOT_APPROVED'
    assert client.post(f"{API}/plans/{plan['id']}/approve", headers=token('ana.arquitecta') | {'If-Match': '5'}).status_code == 409
    assert client.post(f"{API}/plans/{plan['id']}/approve", headers=token('ana.arquitecta') | {'If-Match': '1'}).json()['status'] == 'approved'
    # a new plan version supersedes the previous one
    plan2 = _plan(client, token, scan)
    assert plan2['version'] == 2
    assert client.get(f"{API}/plans/{plan['id']}", headers=token('diego.dev')).json()['status'] == 'superseded'
    assert client.post(f"{API}/plans/{plan['id']}/preview", headers=token('diego.dev')).json()['code'] == 'INVALID_STATE'


def test_quarkus_plan_uses_quarkus_profile(client, token, pilot_repo, monkeypatch):
    _, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    plan = _plan(client, token, scan, 'quarkus')
    rt = next(s for s in plan['steps'] if s['key'] == 'runtime-quarkus')
    assert 'camel-quarkus-jms' in rt['recipe']['yaml'] and 'quarkus-camel-bom' in rt['recipe']['yaml']
    assert 'camel-quarkus-direct' in rt['recipe']['yaml'] and 'camel-quarkus-bean' in rt['recipe']['yaml'] and 'camel-quarkus-log' in rt['recipe']['yaml']
    assert 'Application.java' not in rt['recipe']['yaml'] and 'camel.main.routes-include-pattern=classpath:camel/routes.xml' in rt['recipe']['yaml']


def test_unmapped_component_creates_blocking_finding(client, token, pilot_repo, monkeypatch):
    from mf.db import session
    from mf.models import Module
    _, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    with session() as s:
        mod = s.query(Module).filter(Module.scan_id == uuid.UUID(scan['id']), Module.artifact_id == 'orders-routes').one()
        # exists in camel-bom 4.14.0 but no starter in camel-spring-boot-bom 4.14.9 -> UNMAPPED (runtime step)
        mod.dependencies = mod.dependencies + [{'groupId': 'org.apache.camel', 'artifactId': 'camel-attachments', 'line': 42, 'scope': 'compile',
                                                'versionObserved': None, 'versionResolved': '2.24.3', 'versionSource': 'managed-local'}]
        s.commit()
    plan = _plan(client, token, scan)
    rt = next(s for s in plan['steps'] if s['key'] == 'runtime-spring')
    assert rt['state'] == 'blocked' and any('COMPONENTS_MAPPED' == p['code'] and not p['ok'] for p in rt['preconditions'])
    unmapped = find(client, token, scan['id'], rule='UNMAPPED_COMPONENT')
    assert len(unmapped) == 1 and unmapped[0]['blocking'] and unmapped[0]['evidence'] == 'org.apache.camel:camel-attachments'


def test_unknown_camel_artifact_blocks_the_camel_step(client, token, pilot_repo, monkeypatch):
    """Doc 19: an org.apache.camel artifact absent from the target camel-bom is never written with a Camel 4 version."""
    from mf.db import session
    from mf.models import Module
    _, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    with session() as s:
        mod = s.query(Module).filter(Module.scan_id == uuid.UUID(scan['id']), Module.artifact_id == 'orders-routes').one()
        mod.dependencies = mod.dependencies + [{'groupId': 'org.apache.camel', 'artifactId': 'camel-xmljson', 'line': 42, 'scope': 'compile',
                                                'versionObserved': None, 'versionResolved': '2.24.3', 'versionSource': 'managed-local'}]
        s.commit()
    plan = _plan(client, token, scan)
    camel = next(s for s in plan['steps'] if s['key'] == 'camel-3-to-4')
    assert camel['state'] == 'blocked' and any(p['code'] == 'INVALID_CAMEL_COMPONENT_MAPPING' and not p['ok'] for p in camel['preconditions'])
    f = find(client, token, scan['id'], rule='CAMEL_COMPONENT_COMPAT')
    assert len(f) == 1 and f[0]['blocking'] and f[0]['evidence'] == 'org.apache.camel:camel-xmljson INVALID_CAMEL_COMPONENT_MAPPING'


def test_profiles_and_rules_admin(client, token):
    profs = client.get(f'{API}/profiles', headers=token('diego.dev')).json()
    assert {p['runtime'] for p in profs} == {'spring', 'quarkus'} and all(p['status'] == 'validated' for p in profs)
    spring = next(p for p in profs if p['runtime'] == 'spring')
    body = {k: spring[k] for k in ('key', 'name', 'runtime', 'javaVersion', 'camelVersion', 'runtimeVersion', 'bomCoordinates', 'tooling', 'dependencyMapping')}
    assert client.post(f'{API}/profiles', headers=token('ana.arquitecta'), json=body).status_code == 403
    assert client.post(f'{API}/profiles', headers=token('admin'), json=body | {'bomCoordinates': ['a:b:LATEST']}).status_code == 422
    new = client.post(f'{API}/profiles', headers=token('admin'), json=body)
    assert new.status_code == 201 and new.json()['version'] == 4 and new.json()['status'] == 'draft'
    assert client.post(f"{API}/profiles/{new.json()['id']}/status", headers=token('admin'), params={'status': 'validated'}).json()['status'] == 'validated'
    assert client.post(f"{API}/profiles/{new.json()['id']}/status", headers=token('admin'), params={'status': 'validated'}).status_code == 409

    cats = client.get(f'{API}/rules', headers=token('diego.dev')).json()
    active = next(c for c in cats if c['status'] == 'active')
    bad = [r | {'examples': {'positive': ['nada que ver'], 'negative': []}} if r['id'] == 'JMS' else r for r in active['rules']]
    r = client.post(f'{API}/rules', headers=token('admin'), json={'catalogVersion': 'bad-1', 'rules': bad})
    assert r.status_code == 422 and any('JMS' in e for e in r.json()['details']['errors'])
    assert client.post(f'{API}/rules', headers=token('admin'), json={'catalogVersion': active['version'], 'rules': active['rules']}).status_code == 409
    new_cat = client.post(f'{API}/rules', headers=token('admin'), json={'catalogVersion': 'test-2', 'rules': active['rules']})
    assert new_cat.status_code == 201 and new_cat.json()['status'] == 'draft'
    assert client.post(f"{API}/rules/{new_cat.json()['id']}/activate", headers=token('admin')).json()['status'] == 'active'
    statuses = {c['version']: c['status'] for c in client.get(f'{API}/rules', headers=token('admin')).json()}
    assert statuses[active['version']] == 'retired'
    # restore the original catalog as active for other tests
    from mf.db import session
    from mf.models import RuleCatalog
    with session() as s:
        for c in s.query(RuleCatalog):
            c.status = 'active' if c.version == active['version'] else 'retired'
        s.commit()


def test_audit_export_csv(client, token, pilot_repo):
    pid = new_project(client, token, pilot_repo)
    r = client.get(f'{API}/audit/export', headers=token('aldo.auditor'), params={'projectId': pid})
    assert r.status_code == 200 and r.text.startswith('id,at,project_id') and 'project.create' in r.text


def test_openapi_is_generated(client):
    spec = client.get(f'{API}/openapi.json').json()
    assert '/api/v1/findings/{finding_id}/review' in spec['paths'] and 'FindingOut' in spec['components']['schemas']


def test_catalog_file_change_requires_new_version(monkeypatch):
    """A modified rules file with the same version must not be silently ignored (stale rules in the worker)."""
    import pytest
    from mf.db import session
    from mf import profiles
    from mf.analysis import scanner
    real = scanner.load_catalog()
    changed = dict(real, digest='0' * 64)
    monkeypatch.setattr(scanner, 'load_catalog', lambda path=None: changed)
    with session() as s, pytest.raises(RuntimeError, match='suba la versión'):
        profiles.seed(s)


def test_profile_change_requires_new_version(monkeypatch):
    """Editing a seeded profile without bumping its version must fail (stale mappings in the DB otherwise)."""
    import pytest
    from mf import profiles
    from mf.db import session
    changed = [dict(p, dependency_mapping=dict(p['dependency_mapping'], version='9.9.9')) for p in profiles.PROFILES]
    monkeypatch.setattr(profiles, 'PROFILES', changed)
    with session() as s, pytest.raises(RuntimeError, match='inmutables'):
        profiles.seed(s)


def test_effort_estimate_and_calibration(client, token, pilot_repo, monkeypatch):
    from helpers import scanned_project
    pid, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    est = client.get(f"{API}/scans/{scan['id']}/effort-estimate", headers=token('aldo.auditor')).json()
    rows = {r['category']: r for r in est['rows']}
    assert rows['inter-context']['units'] == 1 and rows['equivalence:JMS']['units'] == 1 and rows['fixed']['units'] == 1
    assert est['complete'] is False and all(r['hoursPerUnit'] is None for r in est['rows'])  # nothing invented
    vm = client.get(f"{API}/scans/{scan['id']}/findings", headers=token('ana.arquitecta'), params={'rule': 'CAMEL_VM'}).json()['items'][0]
    r = client.patch(f"{API}/findings/{vm['id']}/review", headers=token('ana.arquitecta') | {'If-Match': str(vm['version'])},
                     json={'decision': 'resolved', 'reason': 'Rediseñado con un broker interno', 'minutes': 180})
    assert r.status_code == 200
    assert client.post(f'{API}/projects/{pid}/effort', headers=token('diego.dev'), json={'category': 'inter-context', 'minutes': 60}).status_code == 201
    assert client.post(f'{API}/projects/{pid}/effort', headers=token('aldo.auditor'), json={'category': 'fixed', 'minutes': 60}).status_code == 403
    assert client.post(f'{API}/projects/{pid}/effort', headers=token('diego.dev'), json={'category': 'nope', 'minutes': 5}).status_code == 422
    est = client.get(f"{API}/scans/{scan['id']}/effort-estimate", headers=token('aldo.auditor')).json()
    row = next(r for r in est['rows'] if r['category'] == 'inter-context')
    assert row['hoursPerUnit'] == 2.0 and row['source'].startswith('calibrado (2') and row['subtotalHours'] == 2.0  # median of 3h and 1h
    assert len(client.get(f'{API}/projects/{pid}/effort', headers=token('aldo.auditor')).json()['items']) == 2
    assert est['complexity']['score'] > 0 and 'no son horas' in est['complexity']['note']


def test_metrics_and_plugins(client, token):
    """improvements/08 and /10."""
    assert client.get(f'{API}/metrics', headers=token('ana.arquitecta')).status_code == 403  # organisation-wide: admin only
    r = client.get(f'{API}/metrics', headers=token('admin'))
    assert r.status_code == 200 and r.headers['content-type'].startswith('text/plain')
    assert '# TYPE mf_jobs_started_total counter' in r.text and 'mf_recipes_applied_total' in r.text
    plugins = client.get(f'{API}/plugins', headers=token('aldo.auditor')).json()
    ids = {p['id'] for p in plugins}
    assert {'target.spring', 'target.quarkus', 'migration.camel2to4', 'build.maven'} <= ids
    assert all({'id', 'version', 'capabilities', 'supportedSources', 'supportedTargets', 'dependencies'} <= set(p) for p in plugins)

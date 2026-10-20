"""tomcat-war on the platform: a Fuse/Karaf workspace is recognised, tomcat-war is recommended and runs as a workspace
migration in the worker (engine of docs/19_TOMCAT_WAR.md, Maven faked)."""
import shutil
from pathlib import Path

from conftest import ROOT
from helpers import FakeMaven, install, new_project, run_job

FUSE = ROOT / 'examples' / 'fuse-blueprint'


def test_fuse_workspace_is_recommended_and_migrated_to_tomcat_war(client, token, tmp_path, monkeypatch):
    fake = install(monkeypatch, FakeMaven(effective_ok=False))
    orig = fake.mvn
    built = []

    def mvn(ctx, cwd, goals, name, timeout=None, java_home=None):
        if name == 'tomcat-war-package':                      # mvn -DskipTests package on the migrated workspace
            built.append((list(goals), Path(cwd).name))
            war = Path(cwd) / 'webapp' / 'target' / 'ROOT.war'
            war.parent.mkdir(parents=True, exist_ok=True)
            war.write_bytes(b'PK\x05\x06' + b'\x00' * 18)
            return fake._result(ctx, name, 0, '[INFO] BUILD SUCCESS')
        return orig(ctx, cwd, goals, name, timeout)
    monkeypatch.setattr('mf.worker.mavenops.mvn', mvn)
    from mf.config import get_settings
    base = Path(get_settings().local_source_roots.split(',')[0].strip()) / f'fuse-{tmp_path.name}'
    shutil.copytree(FUSE, base)
    try:
        pid = new_project(client, token, str(base))
        r = client.post(f'/api/v1/projects/{pid}/scans', headers=token('diego.dev'), json={}).json()
        run_job(r['jobId'])
        scan = client.get(f"/api/v1/scans/{r['scanId']}", headers=token('diego.dev')).json()
        ws = scan['summary']['workspace']
        rec = ws['shared']['recommendedTarget']
        assert rec['target'] == 'tomcat-war' and rec['count'] == '3/3' and 'ARCHITECTURAL_MIGRATION_REQUIRED' in rec['reason']

        url = f"/api/v1/scans/{scan['id']}/workspace-migrations"
        bad = client.post(url, headers=token('ana.arquitecta'), json={})                    # Spring/Quarkus need a profile
        assert bad.status_code == 422
        over = client.post(url, headers=token('ana.arquitecta'), json={'target': 'tomcat-war', 'config': {'overlay': '../../etc'}})
        assert over.status_code == 422 and 'overlay' in over.text                          # a server folder: never from the platform
        badpatch = client.post(url, headers=token('ana.arquitecta'), json={'target': 'tomcat-war', 'config': {'patches': [{'name': 'x'}]}})
        assert badpatch.status_code == 422 and 'Parche incompleto' in badpatch.text
        acc = client.post(url, headers=token('ana.arquitecta'),                              # no Camel 2->3 decision needed
                          json={'target': 'tomcat-war', 'config': {'groupId': 'com.cliente.migrated', 'version': '2.0.0'}})
        assert acc.status_code == 202, acc.text
        run_job(acc.json()['jobId'])
        wm = client.get(f"/api/v1/workspace-migrations/{acc.json()['workspaceMigrationId']}", headers=token('diego.dev')).json()
        assert wm['target'] == 'tomcat-war' and wm['profileId'] is None and wm['state'] in ('succeeded', 'partial_success'), wm
        s = wm['summary']
        assert s['build']['status'] == 'PASS' and built == [(['-DskipTests', 'package'], 'migrated-workspace')]
        assert s['counts']['modules'] == 3 and s['migrationWaves'][0]['wave'] == 'WAR'
        assert wm['config'] == {'groupId': 'com.cliente.migrated', 'version': '2.0.0', '_verifyRoutes': True} and s['clientConfig']
        # route structure needs the real build output (WEB-INF/lib): with the faked Maven it is not executable, never a fake PASS
        assert s['equivalence']['routeStructure']['status'] == 'NOT_EXECUTABLE' and s['equivalence']['g4'] == 'REVIEW'
        assert s['equivalence']['responses']['status'] == 'NOT_RUN'
        assert s['configKeys']['status'] in ('PASS', 'REVIEW') and 'missing' in s['configKeys']
        assert set(wm['results']) == {'common-services', 'orders-api', 'web-servlets'}
        assert {k.split('.', 1)[1] for k in s['artifacts']} >= {'ROOT.war', 'migrated-workspace.zip', 'tomcat-war-report.md', 'build.log'}
        retry = client.post(f"/api/v1/workspace-migrations/{wm['id']}/retry", headers=token('ana.arquitecta'), json={})
        assert retry.status_code == 409                                                      # one WAR: no per-project retry
    finally:
        shutil.rmtree(base, ignore_errors=True)

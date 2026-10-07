"""Fakes for Maven/OpenRewrite so product tests run offline in seconds."""
import re
import uuid
from pathlib import Path

from mf.worker import mavenops, runner, tasks


class FakeMaven:
    """Simulates recipes by editing files the way the real recipes would for the pilot fixture."""

    def __init__(self, fail_steps=(), compile_ok=True, effective_ok=False, slow=0, model_ok=True):
        self.fail_steps, self.compile_ok, self.effective_ok, self.slow = set(fail_steps), compile_ok, effective_ok, slow
        self.model_ok = model_ok
        self.calls = []

    def _result(self, ctx, name, code, text):
        p = ctx.workdir / 'logs' / f'{name}.log'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return runner.Result(code, 5, p)

    def mvn(self, ctx, cwd, goals, name, timeout=None):
        self.calls.append((name, tuple(goals)))
        if self.slow:  # real subprocess so cancellation/timeout paths are exercised
            return runner.run(['sleep', str(self.slow)], cwd, ctx, name=name, timeout=timeout or 600)
        if goals == ['validate']:
            ok = self.model_ok(cwd) if callable(self.model_ok) else self.model_ok
            return self._result(ctx, name, 0 if ok else 1, '[INFO] BUILD SUCCESS' if ok else
                                "[ERROR] 'dependencies.dependency.version' for org.apache.camel:camel-cdi:jar is missing.")
        if 'install' in goals:
            return self._result(ctx, name, 0 if self.compile_ok else 1, '[INFO] BUILD SUCCESS (install)')
        if 'test-compile' in goals or ('compile' in goals and 'test' not in goals):
            return self._result(ctx, name, 0 if self.compile_ok else 1, '[INFO] BUILD SUCCESS password=supersecret' if self.compile_ok else '[ERROR] fail')
        if 'test' in goals:
            rep = Path(cwd) / 'orders-routes' / 'target' / 'surefire-reports'
            rep.mkdir(parents=True, exist_ok=True)
            (rep / 'TEST-x.xml').write_text('<testsuite tests="3" failures="0" errors="0" skipped="0"/>')
            return self._result(ctx, name, 0, '[INFO] Tests run: 3')
        if any('effective-pom' in g for g in goals):
            if self.effective_ok:
                out = Path(next(g for g in goals if g.startswith('-Doutput='))[9:])
                out.write_text('<projects><project><groupId>demo.pilot</groupId><artifactId>orders-routes</artifactId>'
                               '<properties><maven.compiler.source>1.8</maven.compiler.source></properties>'
                               '<dependencies><dependency><groupId>org.apache.camel</groupId><artifactId>camel-core</artifactId>'
                               '<version>2.24.3</version></dependency></dependencies></project></projects>')
                return self._result(ctx, name, 0, 'ok')
            return self._result(ctx, name, 1, '[ERROR] Could not resolve (simulado sin red)')
        return self._result(ctx, name, 1, 'unexpected')

    def rewrite(self, ctx, repo, goal, *, plugin_version, artifacts, active, config, name):
        self.calls.append((name, tuple(active)))
        repo = Path(repo)
        key = name.replace('rewrite-', '')
        if goal == 'dryRun':
            patch = repo / 'target' / 'rewrite' / 'rewrite.patch'
            patch.parent.mkdir(parents=True, exist_ok=True)
            patch.write_text('diff --git a/pom.xml b/pom.xml\n--- a/pom.xml\n+++ b/pom.xml\n'
                             '@@ -1,1 +1,1 @@ org.apache.camel.upgrade.CamelMigrationRecipe\n-<camel.version>2.24.3</camel.version>\n'
                             '+<camel.version>4.14.0</camel.version>\n')
            return self._result(ctx, name, 0, '[WARNING] dry run')
        if key in self.fail_steps:
            (repo / 'pom.xml').write_text('half-applied garbage')  # partial change that must be rolled back
            return self._result(ctx, name, 1, '[ERROR] recipe crashed')
        pom = repo / 'pom.xml'
        if 'mf.camel.Camel4Artifacts' in active:  # renames/removals decided by the compatibility registry (doc 19)
            yml = Path(config).read_text()
            block = yml.split('name: mf.camel.Camel4Artifacts', 1)[1].split('\n---', 1)[0]
            renames = re.findall(r'oldArtifactId: "?([\w.-]+)"?\n(?:.*\n)*?\s+newArtifactId: "?([\w.-]+)"?', block)
            removes = re.findall(r'RemoveDependency:\n\s+groupId: "?[\w.]+"?\n\s+artifactId: "?([\w.-]+)"?', block)
            for p in repo.rglob('pom.xml'):
                t = p.read_text()
                for old, new in renames:
                    t = t.replace(f'<artifactId>{old}</artifactId>', f'<artifactId>{new}</artifactId>')
                for old in removes:
                    t = re.sub(r'<dependency>\s*<groupId>org\.apache\.camel</groupId>\s*<artifactId>' + re.escape(old) + r'</artifactId>.*?</dependency>', '', t, flags=re.S)
                p.write_text(t)
        if 'org.apache.camel.upgrade.CamelMigrationRecipe' in active:
            pom.write_text(pom.read_text().replace('<camel.version>2.24.3</camel.version>', '<camel.version>4.14.0</camel.version>'))
        if 'org.openrewrite.java.migrate.jakarta.JavaxJmsToJakartaJms' in active:
            f = repo / 'orders-routes/src/main/java/demo/orders/JmsConfig.java'
            f.write_text(f.read_text().replace('javax.jms.', 'jakarta.jms.'))
        if any(a.startswith('mf.runtime.') for a in active):
            yml = Path(config).read_text()
            m = re.search(r'relativeFileName: "([^"]+Application.java)"', yml)
            if m:
                (repo / m[1]).write_text('package demo.orders;\npublic class Application {}\n')
        return self._result(ctx, name, 0, '[INFO] applied')


def install(monkeypatch, fake: FakeMaven):
    monkeypatch.setattr(mavenops, 'mvn', fake.mvn)
    monkeypatch.setattr(mavenops, 'rewrite', fake.rewrite)
    return fake


def run_job(job_id):
    tasks.run_job(str(job_id))


def new_project(client, token, source, owner='ana.arquitecta', members=None, name=None, source_type='local'):
    """source = local folder path (default), upload id (zip) or URL (git)."""
    field = {'local': 'localPath', 'zip': 'uploadId', 'git': 'repositoryUrl'}[source_type]
    r = client.post('/api/v1/projects', headers=token(owner),
                    json={'name': name or f'Orders {uuid.uuid4().hex[:6]}', 'sourceType': source_type, field: source, 'targetRuntime': 'spring'})
    assert r.status_code == 201, r.text
    pid = r.json()['id']
    for user, role in (members if members is not None else {'diego.dev': 'developer', 'rosa.revisora': 'reviewer', 'aldo.auditor': 'auditor'}).items():
        assert client.put(f'/api/v1/projects/{pid}/members', headers=token(owner), json={'subjectId': user, 'role': role}).status_code == 200
    return pid


def scanned_project(client, token, pilot_repo, monkeypatch, effective_ok=True):
    install(monkeypatch, FakeMaven(effective_ok=effective_ok))
    pid = new_project(client, token, pilot_repo)
    r = client.post(f'/api/v1/projects/{pid}/scans', headers=token('diego.dev'), json={})
    assert r.status_code == 202, r.text
    run_job(r.json()['jobId'])
    scan = client.get(f"/api/v1/scans/{r.json()['scanId']}", headers=token('diego.dev')).json()
    assert scan['status'] == 'succeeded', scan
    return pid, scan

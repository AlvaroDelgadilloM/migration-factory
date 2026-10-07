"""CLI (migrate.py) over the engine: exit codes, outputs, source untouched, dry-run, bounded autofix.
Maven/OpenRewrite are faked here; a real run is documented in ESTADO_IMPLEMENTACION.md."""
import json
import shutil
from pathlib import Path

import pytest

from conftest import ROOT
from helpers import FakeMaven, install
from mf import cli_tool
from mf.worker import runner
from mf.worker.source import content_hash

PILOT = ROOT / 'examples' / 'pilot-orders'
ACCEPT = 'CAMEL2_VERSION=Revisado contra la guía 2→3 en la fixture'


@pytest.fixture
def project(tmp_path):
    p = tmp_path / 'legacy'
    shutil.copytree(PILOT, p)
    return p


def test_analyze_outputs_and_exit_codes(project, tmp_path, monkeypatch, capsys):
    install(monkeypatch, FakeMaven(effective_ok=True))
    assert cli_tool.main(['analyze', str(project), '--output', str(tmp_path / 'a')]) == 0
    data = json.loads((tmp_path / 'a' / 'analysis.json').read_text())
    assert data['overview']['camel'] == ['2.24.3'] and data['overview']['runtime'] == 'jboss'
    assert {i['type'] for i in data['integrations']} == {'JMS', 'DB', 'INTER_CONTEXT'}
    assert data['mavenResolution'] == 'maven-effective'
    assert cli_tool.main(['analyze', str(project), '--fail-on-critical']) == 3
    assert cli_tool.main(['analyze', str(tmp_path / 'missing')]) == 4
    (tmp_path / 'empty').mkdir()
    assert cli_tool.main(['analyze', str(tmp_path / 'empty')]) == 4  # no pom.xml


def test_plan_requires_explicit_acceptance(project, monkeypatch, capsys):
    install(monkeypatch, FakeMaven(effective_ok=True))
    assert cli_tool.main(['plan', str(project), '--fail-on-critical']) == 3
    assert '| camel-3-to-4 | BLOCKED |' in capsys.readouterr().out
    assert cli_tool.main(['plan', str(project), '--accept', 'CAMEL2_VERSION=corto']) == 4
    cli_tool.main(['plan', str(project), '--accept', ACCEPT, '--java', '21'])
    out = capsys.readouterr().out
    assert '| camel-3-to-4 | AUTOMATIC_WITH_REVIEW | ready |' in out and 'UpgradeToJava21' in out
    assert '| manual-camel-vm | BLOCKED |' in out and '%' not in out.split('##')[0].replace('%)', '')


def test_migrate_writes_new_output_and_never_touches_source(project, tmp_path, monkeypatch):
    install(monkeypatch, FakeMaven(effective_ok=True))
    before = content_hash(project)
    out = tmp_path / 'out'
    code = cli_tool.main(['migrate', str(project), '--output', str(out), '--accept', ACCEPT])
    assert code == 0
    assert content_hash(project) == before and not (project / '.git').exists()
    assert sorted(p.name for p in out.iterdir()) == ['logs', 'migrated-project', 'migrated-project.zip', 'reports']
    import zipfile
    z = zipfile.ZipFile(out / 'migrated-project.zip')  # doc 19: final candidate + .migration/
    assert json.loads(z.read('.migration/manifest.json'))['sourceType'] == 'LOCAL' and 'pom.xml' in z.namelist()
    assert {'analysis.json', 'migration-plan.md', 'migration-report.md', 'manual-actions.md', 'effort-estimate.md', 'effort-estimate.json'} <= {p.name for p in (out / 'reports').iterdir()}
    names = {p.name for p in (out / 'reports').iterdir()}
    assert {'executive-summary.md', 'technical-analysis.md', 'integration-inventory.md', 'security-report.md', 'build-report.md',
            'changes.md', 'changes.json', 'sbom.cdx.json', 'checksums.sha256'} <= names  # improvements/11, 12, 14
    sums = (out / 'reports' / 'checksums.sha256').read_text()
    assert '  migrated-project/pom.xml' in sums and '  reports/sbom.cdx.json' in sums
    bom = json.loads((out / 'reports' / 'sbom.cdx.json').read_text())
    assert bom['bomFormat'] == 'CycloneDX' and isinstance(bom['components'], list)
    rep = json.loads((out / 'reports' / 'migration-report.json').read_text())
    assert rep['trace']['migrationRules'] and rep['trace']['sourceSnapshot'] and rep['trace']['engineVersion']
    analysis = json.loads((out / 'reports' / 'analysis.json').read_text())  # improvements/02
    assert {'runtime', 'java', 'camel', 'risks', 'migrationHints', 'graph'} <= set(analysis)
    assert all('migrationAction' in f for f in analysis['findings']) and analysis['graph']['nodes']
    est = json.loads((out / 'reports' / 'effort-estimate.json').read_text())
    assert est['complexity']['score'] > 0
    assert any(r['category'] == 'diffReview' and r['units'] > 0 for r in est['rows']) and est['complete'] is False
    mp = out / 'migrated-project'
    assert not (mp / '.git').exists() and (mp / 'orders-routes/src/main/java/demo/orders/Application.java').exists()
    assert '4.14.0' in (mp / 'pom.xml').read_text()
    manual = (out / 'reports' / 'manual-actions.md').read_text()
    assert 'CAMEL_VM' in manual and 'CRITICAL' in manual and 'CAMEL2_VERSION' not in manual
    report = (out / 'reports' / 'migration-report.md').read_text()
    assert 'mvn compile: SUCCESS' in report and 'readiness' in report  # explicitly states there is no readiness percentage
    # output must be new and outside the source
    assert cli_tool.main(['migrate', str(project), '--output', str(out), '--accept', ACCEPT]) == 4
    assert cli_tool.main(['migrate', str(project), '--output', str(project / 'out'), '--accept', ACCEPT]) == 4
    assert cli_tool.main(['migrate', str(project), '--output', str(tmp_path / 'o2'), '--accept', ACCEPT, '--fail-on-critical']) == 3


def test_transforms_wiring_smoke_test_and_sensitive_files(project, tmp_path, monkeypatch):
    install(monkeypatch, FakeMaven(effective_ok=True))
    route = project / 'orders-routes/src/main/java/demo/orders/OrdersRoute.java'
    route.write_text(route.read_text().replace('.to("jdbc:ordersDS");', '.to("jdbc:ordersDS").to("http4://payments.internal/api/pay");'))
    props = project / 'orders-routes/src/main/resources/app.properties'
    props.write_text('db.user=orders\ndb.password=Sup3rS3cret\n')
    (project / 'orders-routes/src/main/resources/server.jks').write_bytes(b'\x00keystore')
    out = tmp_path / 'out'
    assert cli_tool.main(['migrate', str(project), '--output', str(out), '--accept', ACCEPT, '--skip-baseline']) == 0
    mp = out / 'migrated-project/orders-routes'
    assert '"{{services.payments-internal-api.url}}"' in (mp / 'src/main/java/demo/orders/OrdersRoute.java').read_text()
    yml = (mp / 'src/main/resources/application.yml').read_text()
    assert 'url: ${SERVICES_PAYMENTS_INTERNAL_API_URL:http://payments.internal/api/pay}' in yml
    assert 'Sup3rS3cret' not in (mp / 'src/main/resources/app.properties').read_text()
    assert '${MF_SECRET_DB_PASSWORD}' in (mp / 'src/main/resources/app.properties').read_text()
    assert not (mp / 'src/main/resources/server.jks').exists() and (project / 'orders-routes/src/main/resources/server.jks').exists()
    assert 'Sup3rS3cret' not in ''.join(p.read_text(errors='ignore') for p in out.rglob('*') if p.is_file())
    plan = (out / 'reports' / 'migration-plan.json').read_text()
    assert 'MigratedRoutesConfig.java' in plan and 'MigratedRoutesSmokeTest.java' in plan and 'application.yml' in plan
    manual = (out / 'reports' / 'manual-actions.md').read_text()
    assert 'server.jks' in manual and 'SENSITIVE_FILE' in manual
    # improvements/05+14: every applied step is in the ledger with rule, lines, before/after, level and rollback patch
    changes = json.loads((out / 'reports' / 'changes.json').read_text())
    ep = next(e for e in changes if e['step'] == 'endpoints-config')
    assert ep['confidence'] == 'REVIEW' and 'CAMEL_HTTP4_TO_HTTP' in ep['rules'] and ep['rollback']['available']
    c = next(c for c in ep['changes'] if c['file'].endswith('OrdersRoute.java'))
    assert 'http4://payments.internal' in c['before'] and 'services.payments-internal-api.url' in c['after']
    assert (out / 'reports' / ep['patch']).read_text().startswith('diff --git')
    assert 'Sup3rS3cret' not in json.dumps(changes)


def test_confidence_threshold_requires_approval(project, tmp_path, monkeypatch):
    install(monkeypatch, FakeMaven(effective_ok=True))
    out = tmp_path / 'strict'
    assert cli_tool.main(['migrate', str(project), '--output', str(out), '--accept', ACCEPT, '--skip-baseline', '--auto-apply-up-to', 'AUTO_TEST']) == 2
    steps = {s['key']: s for s in json.loads((out / 'reports' / 'migration-report.json').read_text())['steps']}
    assert steps['runtime-spring']['state'] == 'PENDING_APPROVAL' and '--approve runtime-spring' in steps['runtime-spring']['reason']
    assert steps['camel-3-to-4']['state'] == 'APPLIED'  # AUTO_TEST is within the threshold
    out2 = tmp_path / 'approved'
    assert cli_tool.main(['migrate', str(project), '--output', str(out2), '--accept', ACCEPT, '--skip-baseline', '--auto-apply-up-to', 'AUTO_TEST',
                          '--approve', 'runtime-spring']) == 0


def test_same_input_same_candidate(project, tmp_path, monkeypatch):
    """improvements/04: running the same job twice yields the same candidate."""
    install(monkeypatch, FakeMaven(effective_ok=True))
    for o in ('r1', 'r2'):
        assert cli_tool.main(['migrate', str(project), '--output', str(tmp_path / o), '--accept', ACCEPT, '--skip-baseline']) == 0
    assert content_hash(tmp_path / 'r1' / 'migrated-project') == content_hash(tmp_path / 'r2' / 'migrated-project')
    assert (tmp_path / 'r1/reports/candidate.patch').read_text() == (tmp_path / 'r2/reports/candidate.patch').read_text()


def test_dry_run_only_writes_plan_and_patch(project, tmp_path, monkeypatch):
    install(monkeypatch, FakeMaven(effective_ok=True))
    out = tmp_path / 'dry'
    assert cli_tool.main(['migrate', str(project), '--output', str(out), '--accept', ACCEPT, '--dry-run']) == 0
    assert not (out / 'migrated-project').exists()
    assert 'CamelMigrationRecipe' in (out / 'reports' / 'dry-run.patch').read_text()


def test_known_fixes_parser():
    mapping = {'coreComponent': 'camel-{}-starter'}
    log = ('[ERROR] /x/A.java:[3,17] package javax.jms does not exist\n'
           'Caused by: org.apache.camel.NoSuchEndpointException: No endpoint could be found for: direct://process-order\n'
           'package javax.naming.spi does not exist\nNo component found with scheme: weird')
    fixes = cli_tool.known_fixes(log, mapping)
    assert {f['kind'] for f in fixes} == {'jakarta-recipe', 'add-component'}
    assert next(f for f in fixes if f['kind'] == 'add-component')['artifact'] == 'camel-direct-starter'
    assert not any('naming' in f['trigger'] or 'weird' in f['trigger'] for f in fixes)  # Java SE / unknown: manual


def test_autofix_bounded_and_recorded(project, tmp_path, monkeypatch):
    fake = install(monkeypatch, FakeMaven(effective_ok=True))
    calls = {'test': 0}
    orig_mvn = fake.mvn

    def mvn(ctx, cwd, goals, name, timeout=None):
        if name.startswith('candidate-compile-'):
            calls['test'] += 1
            if calls['test'] == 1:
                return fake._result(ctx, name, 1, 'NoSuchEndpointException: No endpoint could be found for: direct://process-order')
        return orig_mvn(ctx, cwd, goals, name, timeout)
    orig_rw = fake.rewrite

    def rewrite(ctx, repo, goal, **kw):
        if kw['name'].startswith('autofix-'):
            pom = Path(repo) / 'orders-routes' / 'pom.xml'
            pom.write_text(pom.read_text().replace('</dependencies>', '<!-- camel-direct-starter --></dependencies>'))
            return fake._result(ctx, kw['name'], 0, 'ok')
        return orig_rw(ctx, repo, goal, **kw)
    monkeypatch.setattr('mf.worker.mavenops.mvn', mvn)
    monkeypatch.setattr('mf.worker.mavenops.rewrite', rewrite)
    out = tmp_path / 'fix'
    assert cli_tool.main(['migrate', str(project), '--output', str(out), '--accept', ACCEPT, '--skip-baseline']) == 0
    rep = json.loads((out / 'reports' / 'migration-report.json').read_text())
    assert rep['autofix'][0]['fix']['component'] == 'direct' and rep['autofix'][0]['applied']
    assert [v['status'] for v in rep['validations'] if v.get('name') == 'compile'] == ['FAIL', 'PASS']
    # never loops: with --max-fix-rounds 0 the failure is reported as a build failure (exit 2)
    calls['test'] = 0
    assert cli_tool.main(['migrate', str(project), '--output', str(tmp_path / 'nofix'), '--accept', ACCEPT,
                          '--skip-baseline', '--max-fix-rounds', '0']) == 2


def test_validate_exit_codes(tmp_path, monkeypatch):
    install(monkeypatch, FakeMaven(compile_ok=True))
    proj = tmp_path / 'p'
    shutil.copytree(PILOT, proj)
    assert cli_tool.main(['validate', str(proj)]) == 0
    assert cli_tool.main(['validate', str(tmp_path)]) == 4
    monkeypatch.setattr('mf.worker.mavenops.mvn', lambda ctx, cwd, goals, name, timeout=None:
                        runner.Result(1, 1, _log(ctx, name)))
    assert cli_tool.main(['validate', str(proj)]) == 2


def _log(ctx, name):
    p = ctx.workdir / 'logs' / f'{name}.log'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[ERROR] BUILD FAILURE')
    return p


def test_missing_camel2_decision_is_the_primary_reason(project, tmp_path, monkeypatch):
    """A Camel 2 project without the architect decision reports why, not a generic MANDATORY_RECIPE_NOT_APPLIED."""
    install(monkeypatch, FakeMaven(effective_ok=True))
    out = tmp_path / 'nodecision'
    assert cli_tool.main(['migrate', str(project), '--output', str(out), '--skip-baseline']) == 2
    rep = json.loads((out / 'reports' / 'migration-report.json').read_text())
    assert rep['primaryReason'] == 'CAMEL_2_TO_3_DECISION_REQUIRED' and 'MANDATORY_RECIPE_NOT_APPLIED' in rep['reasonCodes']

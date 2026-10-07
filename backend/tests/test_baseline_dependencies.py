"""32-CORRECCION-BASELINE-DEPENDENCY-RESOLUTION: environment that cannot reproduce the baseline != broken code."""
import json
import shutil

from conftest import ROOT
from helpers import FakeMaven, install, run_job, scanned_project
from mf import cli_tool

PILOT = ROOT / 'examples' / 'pilot-orders'
ACCEPT = 'CAMEL2_VERSION=Revisado contra la guía 2→3 en la fixture'
ORA = ('[ERROR] Failed to execute goal on project orders-routes: Could not resolve dependencies for project demo.pilot:orders-routes:war:1.0.0: '
       'The following artifacts could not be resolved: com.oracle:ojdbc6:jar:11.2.0.4 (absent): Could not find artifact '
       'com.oracle:ojdbc6:jar:11.2.0.4 in mf-approved (https://repo1.maven.org/maven2)\n[ERROR] -> [Help 1]\n')


def with_missing(fake, monkeypatch, log=ORA):
    orig = fake.mvn

    def mvn(ctx, cwd, goals, name, timeout=None):
        if name == 'baseline-compile':
            return fake._result(ctx, name, 1, log)
        return orig(ctx, cwd, goals, name, timeout)
    monkeypatch.setattr('mf.worker.mavenops.mvn', mvn)


def run(tmp_path, *extra):
    out = tmp_path / 'out'
    code = cli_tool.main(['migrate', str(PILOT), '--output', str(out), '--accept', ACCEPT, *extra])
    return code, json.loads((out / 'reports/migration-report.json').read_text()), out


def test_external_low_criticality_dependency_degrades_instead_of_failing(tmp_path, monkeypatch):    # cases 1 and 3
    fake = install(monkeypatch, FakeMaven(effective_ok=True))
    with_missing(fake, monkeypatch)
    code, rep, out = run(tmp_path)
    assert rep['baseline'] == 'BASELINE_BLOCKED_DEPENDENCY' and rep['baselineBuildState'] == 'BUILD_BLOCKED_DEPENDENCY'
    assert rep['baselineStages']['compile'] == 'NOT_EXECUTABLE'                       # §18: Maven never reached javac
    dep = rep['baselineDependencies'][0]
    assert (dep['artifact'], dep['reason'], dep['dependencyCategory'], dep['migrationCriticality']) == \
        ('com.oracle:ojdbc6:jar:11.2.0.4', 'ARTIFACT_NOT_FOUND', 'DATABASE_DRIVER', 'LOW')
    assert rep['migrationMode'] == 'DEGRADED' and rep['baselineDecision']['analysis'] == 'CONTINUE'
    assert rep['status'] == 'PARTIAL_SUCCESS' and code == 0 and rep['primaryReason'] == 'MIGRATION_DEGRADED'
    assert 'BUILD_NOT_EXECUTABLE' in rep['reasonCodes'] and 'CANDIDATE_COMPILE_FAILED' not in rep['reasonCodes']
    assert rep['validationConfidence']['level'] == 'REDUCED' and rep['candidateBuildState'] == 'BUILD_NOT_EXECUTABLE'
    assert rep['rootCause']['primaryReason'] == 'BASELINE_BLOCKED_DEPENDENCY' and rep['rootCause']['artifact'] == 'com.oracle:ojdbc6:jar:11.2.0.4'
    assert any(s['key'] == 'camel-3-to-4' and s['state'] == 'APPLIED' for s in rep['steps'])          # migrated with stubs for the recipes
    assert rep['degradedStubs'] == ['com.oracle:ojdbc6:jar:11.2.0.4'] and not (out / '.m2-degraded').exists()
    assert not any('ojdbc' in p.name for p in (out / 'migrated-project').rglob('*'))                  # stubs never delivered
    br = (out / 'reports/baseline-report.md').read_text()
    for s in ('Baseline status:\nBASELINE_BLOCKED_DEPENDENCY', 'Compiler executed:\nNO', 'Code compile status:\nUNKNOWN', 'Migration:\nDEGRADED',
              'Validation confidence:\nREDUCED', 'ojdbc6 → ojdbc11'):
        assert s in br, s


def test_code_failure_is_baseline_failed_code(tmp_path, monkeypatch):                               # case 2
    fake = install(monkeypatch, FakeMaven(effective_ok=True))
    with_missing(fake, monkeypatch, '[ERROR] COMPILATION ERROR :\n[ERROR] /p/orders-routes/src/main/java/A.java:[3,1] cannot find symbol\n')
    code, rep, _ = run(tmp_path)
    assert rep['baseline'] == 'BASELINE_FAILED_CODE' and rep['status'] == 'BLOCKED' and code == 1 and not rep['baselineDependencies']


def test_framework_dependency_blocks_the_migration(tmp_path, monkeypatch):                         # §14
    fake = install(monkeypatch, FakeMaven(effective_ok=True))
    with_missing(fake, monkeypatch, ORA.replace('com.oracle:ojdbc6:jar:11.2.0.4', 'org.apache.camel:camel-legacy:jar:2.24.3'))
    code, rep, _ = run(tmp_path)
    assert rep['status'] == 'BLOCKED' and rep['primaryReason'] == 'MIGRATION_BLOCKED_BY_BASELINE_DEPENDENCY' and code == 1
    assert rep['baselineDecision']['migration'] == 'BLOCKED' and 'crítica' in rep['baselineDecision']['reason']


def test_medium_criticality_needs_the_specific_flag(tmp_path, monkeypatch):                        # §22-23
    fake = install(monkeypatch, FakeMaven(effective_ok=True))
    with_missing(fake, monkeypatch, ORA.replace('com.oracle:ojdbc6:jar:11.2.0.4', 'org.acme:legacy-util:jar:1.2'))
    assert run(tmp_path)[1]['primaryReason'] == 'MIGRATION_BLOCKED_BY_BASELINE_DEPENDENCY'
    shutil.rmtree(tmp_path / 'out')
    code, rep, _ = run(tmp_path, '--allow-baseline-dependency-failure')
    assert rep['migrationMode'] == 'DEGRADED' and code == 0


def test_internal_artifact_and_waves(tmp_path, monkeypatch):                                       # cases 4 and 5, §21
    from test_workspace import make_workspace
    fake = install(monkeypatch, FakeMaven(effective_ok=True))
    orig = fake.mvn

    def mvn(ctx, cwd, goals, name, timeout=None):
        if name == 'baseline-compile' and 'common-datasource' in str(cwd):
            return fake._result(ctx, name, 1, ORA)
        if name == 'install-common-datasource-migrated':
            return fake._result(ctx, name, 1, ORA)   # the degraded project cannot be built for its dependents
        return orig(ctx, cwd, goals, name, timeout)
    monkeypatch.setattr('mf.worker.mavenops.mvn', mvn)
    base = make_workspace(tmp_path / 'src', {'common-datasource': [], 'common-activemq': [], 'common-epay': ['common-datasource']})
    out = tmp_path / 'ws'
    cli_tool.main(['migrate', str(base), '--workspace', '--output', str(out), '--accept', ACCEPT])
    st = {n: x['result'] for n, x in json.loads((out / 'workspace' / 'workspace-state.json').read_text())['projects'].items()}
    assert st['common-datasource']['status'] == 'PARTIAL_SUCCESS' and st['common-datasource']['migrationMode'] == 'DEGRADED'
    assert st['common-datasource']['artifactAvailable'] is False
    assert st['common-activemq']['status'] in ('SUCCEEDED', 'PARTIAL_SUCCESS')                       # independent project of the wave continues
    assert st['common-epay'] == {'project': 'common-epay', 'status': 'BLOCKED', 'reason': 'BLOCKED_BY_DEPENDENCY', 'blockedBy': ['common-datasource']}
    from mf.baseline import dependency_resolution_classifier as drc
    i = drc.classify(ORA.replace('com.oracle:ojdbc6:jar:11.2.0.4', 'com.company.commons:common-datasource:jar:1.0.0'),
                     internal={'com.company.commons:common-datasource'})
    assert i[0]['reason'] == 'INTERNAL_ARTIFACT_MISSING' and drc.baseline_state(False, [], [], i) == 'BASELINE_BLOCKED_INTERNAL_ARTIFACT'


def test_platform_degraded_execution(client, token, pilot_repo, monkeypatch):
    from test_worker import _ready_plan
    pid, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    plan = _ready_plan(client, token, scan)
    fake = install(monkeypatch, FakeMaven())
    orig = fake.mvn
    monkeypatch.setattr('mf.worker.mavenops.mvn', lambda ctx, cwd, goals, name, timeout=None:
                        fake._result(ctx, name, 1, ORA) if name == 'baseline-compile' else orig(ctx, cwd, goals, name, timeout))
    r = client.post(f"/api/v1/plans/{plan['id']}/executions", headers=token('diego.dev'), json={'mode': 'apply'}).json()
    run_job(r['jobId'])
    ex = client.get(f"/api/v1/executions/{r['executionId']}", headers=token('diego.dev')).json()
    sm = ex['summary']
    assert ex['state'] == 'partial_success' and sm['baseline'] == 'BASELINE_BLOCKED_DEPENDENCY' and sm['migrationMode'] == 'DEGRADED'
    assert sm['validationConfidence'] == 'REDUCED' and sm['rootCause']['artifact'] == 'com.oracle:ojdbc6:jar:11.2.0.4'
    vals = {(v['suite'], v['target']): v for v in client.get(f"/api/v1/executions/{ex['id']}/validations", headers=token('diego.dev')).json()}
    assert vals[('compile', 'baseline')]['status'] == 'BLOCKED' and 'BUILD_BLOCKED_DEPENDENCY' in vals[('compile', 'baseline')]['detail']
    assert vals[('compile', 'candidate')]['status'] == 'NOT_RUN' and 'BUILD_NOT_EXECUTABLE' in vals[('compile', 'candidate')]['detail']
    assert any(s['key'] == 'camel-3-to-4' and s['state'] == 'applied' for s in ex['steps'])

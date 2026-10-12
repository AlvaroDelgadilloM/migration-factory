"""migration-factory-code-review-fixes 43-48 (doc 49 mandatory tests; 43 is in test_preflight.py)."""
import json

from helpers import FakeMaven, install
from mf import camel_compat, cli_tool
from mf.dependency import preflight_gates, stub_leak_guard
from mf.pom import normalizer
from test_baseline_dependencies import ACCEPT, run, with_missing
from test_workspace import make_workspace

T = '4.14.0'
BP = 'src/main/resources/OSGI-INF/blueprint/routes.xml'


def test_gate_keeps_the_planned_cxf_decision(tmp_path):                                               # 44
    ctx = {'usage': {}, 'evidence': {'cxf:bean:': [{'file': BP, 'line': 42}], 'OSGI-INF/blueprint': [{'file': BP, 'line': None}]},
           'endpoints': [], 'packagings': ['bundle'], 'platform': None, 'tests': 0}
    planned = camel_compat.decide('camel-cxf', camel_target=T, **ctx)
    assert planned['detectedUsage'] == 'SOAP' and planned['action'] == 'RENAME'
    (tmp_path / 'pom.xml').write_text('<project><groupId>g</groupId><artifactId>m</artifactId><version>1</version><dependencies>'
                                      '<dependency><groupId>org.apache.camel</groupId><artifactId>camel-cxf</artifactId><version>4.14.0</version></dependency>'
                                      '</dependencies></project>')
    modules = [{'file': 'pom.xml', 'dependencies': [{'groupId': 'org.apache.camel', 'artifactId': 'camel-cxf'}]}]
    g = preflight_gates.component_gate(modules, {}, T, [planned], ctx)
    f = g['findings'][0]
    assert f['code'] != 'CXF_USAGE_AMBIGUOUS' and f['decisionSource'] == 'PLAN' and f['recommendedAction'] == 'REPLACE_WITH camel-cxf-soap'
    assert f['dependencyEvidence'] == planned['dependencyEvidence'] and f['migrationConfidence'] == planned['migrationConfidence']
    assert preflight_gates.component_gate(modules, {}, T)['findings'][0]['code'] == 'CXF_USAGE_AMBIGUOUS'   # what reduced context did
    # a new artifact (the rename target) is decided with exactly the plan's context: same answer as planning would give
    target = [{'file': 'pom.xml', 'dependencies': [{'groupId': 'org.apache.camel', 'artifactId': 'camel-cxf-soap'}]}]
    assert preflight_gates.component_gate(target, {}, T, [planned], ctx)['status'] == 'PASS'
    assert camel_compat.decide('camel-cxf', camel_target=T, **ctx) == planned                        # deterministic for one snapshot


def test_stubbed_model_is_not_real_resolution_and_never_leaks(tmp_path, monkeypatch):                 # 45, 46, 48
    fake = install(monkeypatch, FakeMaven(effective_ok=True))
    with_missing(fake, monkeypatch)
    from mf.worker import mavenops
    overlay_at = {}
    orig = mavenops.mvn

    def spy(ctx, cwd, goals, name, timeout=None):
        overlay_at[name] = mavenops.current_overlay()
        return orig(ctx, cwd, goals, name, timeout)
    monkeypatch.setattr('mf.worker.mavenops.mvn', spy)
    code, rep, out = run(tmp_path)
    cand = {v['name']: v for v in rep['validations'] if v.get('target') == 'candidate'}
    assert cand['maven-model']['status'] == 'PASS_WITH_STUBS' and cand['maven-model']['resolutionMode'] == 'DEGRADED_WITH_STUBS'
    assert cand['maven-model']['stubbedDependencies'] == ['com.oracle:ojdbc6:jar:11.2.0.4']
    vs = rep['validationState']
    assert (vs['candidateModel'], vs['candidateDependencyResolution'], vs['candidateCompile'], vs['candidateCompileValidation'],
            vs['candidateTest'], vs['stubMode'], vs['overall']) == ('PASS_WITH_STUBS', 'BLOCKED_EXTERNAL_DEPENDENCY', 'UNVERIFIED', 'UNVERIFIED',
                                                                  'NOT_RUN', 'DEGRADED_WITH_STUBS', 'PARTIAL_SUCCESS')
    assert rep['status'] == vs['overall'] and code == 0 and 'RESOLUTION_DEGRADED_WITH_STUBS' in rep['reasonCodes']
    g = rep['stubLeakGuard']
    assert g['status'] == 'PASS' and g['evidence'] == {'stubbedDependencies': ['com.oracle:ojdbc6:jar:11.2.0.4'], 'overlayEnabled': False,
                                                       'candidateContainsStubRepo': False, 'finalClasspathContainsStub': False}
    assert overlay_at['candidate-validate'] is None and overlay_at['candidate-maven-model'] is not None  # stubs only for the recipes
    assert rep['stubLeakGuardPackage']['status'] == 'PASS'


def test_a_leaked_stub_blocks_the_build(tmp_path, monkeypatch):                                       # 46
    fake = install(monkeypatch, FakeMaven(effective_ok=True))
    with_missing(fake, monkeypatch)
    monkeypatch.setattr('mf.baseline.stubs.remove_stubs', lambda repo, coords: [])               # simulate a stub left behind
    code, rep, _ = run(tmp_path)
    assert rep['stubLeakGuard']['status'] == 'FAIL' and rep['stubLeakGuard']['evidence']['finalClasspathContainsStub']
    assert rep['primaryReason'] == 'STUB_LEAK_DETECTED' and rep['status'] == 'BLOCKED' and code != 0
    assert not any(v['name'] == 'compile' and v.get('status') == 'PASS' for v in rep['validations'] if v.get('target') == 'candidate')


def test_guard_blocks_a_candidate_carrying_stubs(tmp_path):                                           # 46
    from mf.baseline.stubs import install_stubs
    (tmp_path / 'pom.xml').write_text('<project><repositories><repository><id>x</id><url>file:///w/.m2-degraded</url></repository>'
                                      '</repositories></project>')
    install_stubs(tmp_path / '.m2-degraded', ['com.oracle:ojdbc6:jar:11.2.0.4'])
    g = stub_leak_guard.check(tmp_path, ['com.oracle:ojdbc6:jar:11.2.0.4'])
    assert g['status'] == 'FAIL' and g['code'] == 'STUB_LEAK_DETECTED' and g['evidence']['candidateContainsStubRepo']
    assert {'STUB_FILE_IN_CANDIDATE', 'STUB_REPOSITORY_IN_POM', 'STUB_JAR_IN_CANDIDATE'} <= {x['kind'] for x in g['leaks']}


def test_dedupe_never_drops_different_exclusions_scope_or_optional(tmp_path):                        # 47
    dep = lambda extra='': f'<dependency><groupId>g</groupId><artifactId>a</artifactId><version>1</version>{extra}</dependency>\n'
    for extra, field in (('<exclusions><exclusion><groupId>x</groupId><artifactId>y</artifactId></exclusion></exclusions>', 'exclusions'),
                         ('<scope>test</scope>', 'scope'), ('<optional>true</optional>', 'optional')):
        (tmp_path / 'pom.xml').write_text(f'<project><dependencies>\n{dep(extra)}{dep()}</dependencies></project>\n')
        n = normalizer.normalize(tmp_path)
        assert (tmp_path / 'pom.xml').read_text().count('<artifactId>a</artifactId>') == 2
        assert n['conflicts'] == [{'file': 'pom.xml', 'kind': 'POM_DUPLICATE_SEMANTIC_CONFLICT', 'key': 'g:a', 'fields': [field],
                                   'resolution': 'MANUAL_REVIEW'}]
    (tmp_path / 'pom.xml').write_text(f'<project><dependencies>\n{dep()}{dep()}</dependencies></project>\n')
    assert normalizer.normalize(tmp_path)['changes'] and (tmp_path / 'pom.xml').read_text().count('<artifactId>a</artifactId>') == 1


def test_cli_and_workspace_report_the_same_state(tmp_path, monkeypatch):                              # 48
    install(monkeypatch, FakeMaven(effective_ok=True))
    base = make_workspace(tmp_path / 'src')
    cli_tool.main(['migrate', str(base), '--workspace', '--output', str(tmp_path / 'ws'), '--accept', ACCEPT])
    state = json.loads((tmp_path / 'ws' / 'workspace' / 'workspace-state.json').read_text())
    for name, p in state['projects'].items():
        own = tmp_path / 'ws' / 'projects' / name / 'reports' / 'migration-report.json'
        if own.exists():
            r = json.loads(own.read_text())
            assert p['result']['validationState'] == r['validationState'] and p['result']['status'] == r['validationState']['overall'], name

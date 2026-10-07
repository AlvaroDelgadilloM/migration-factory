"""camel-correcciones-compatibilidad (22-31): the member-phygital shape is fixed or stopped before compile, with the
real root cause. Maven/OpenRewrite are faked: the fake Camel recipe, like the real one, only bumps ${camel.version}."""
import json
import shutil
from pathlib import Path

from conftest import ROOT
from helpers import FakeMaven, install
from mf import cli_tool, compile_errors
from mf.dependency import preflight_gates

PILOT = ROOT / 'examples' / 'pilot-orders'
ACCEPT = 'CAMEL2_VERSION=Revisado contra la guía 2→3 en la fixture'
REST = ('package demo.orders;\nimport org.apache.camel.builder.RouteBuilder;\npublic class ApiDocs extends RouteBuilder {\n'
        '  public void configure() { restConfiguration().component("servlet").apiContextPath("/api-doc"); }\n}\n')


def phygital(base: Path, packaging=None) -> Path:
    p = base / 'member-phygital'
    shutil.copytree(PILOT, p)
    pom = p / 'orders-routes' / 'pom.xml'
    lit = lambda a: f'<dependency><groupId>org.apache.camel</groupId><artifactId>{a}</artifactId><version>2.24.3</version></dependency>'
    # camel-http (Camel 2's HTTP3 client) and camel-http4 side by side: http4 -> http duplicates it (real Camel 2 projects)
    t = pom.read_text().replace('</dependencies>', lit('camel-swagger-java') + lit('camel-jackson') + lit('camel-http') + lit('camel-http4')
                                + '</dependencies>', 1)
    t = t.replace('</project>', '<build><finalName>${artifactId}</finalName></build></project>') if '<build>' not in t else \
        t.replace('<build>', '<build><finalName>${artifactId}</finalName>', 1)
    if packaging:
        t = t.replace('<packaging>war</packaging>', f'<packaging>{packaging}</packaging>')
    pom.write_text(t)
    (p / 'orders-routes/src/main/java/demo/orders/ApiDocs.java').write_text(REST)
    return p


def test_member_phygital_is_normalized_and_passes_every_gate(tmp_path, monkeypatch):       # 22-26, 29, 31
    fake = install(monkeypatch, FakeMaven(effective_ok=True))
    out = tmp_path / 'out'
    assert cli_tool.main(['migrate', str(phygital(tmp_path)), '--output', str(out), '--accept', ACCEPT, '--skip-baseline']) == 0
    rep = json.loads((out / 'reports/migration-report.json').read_text())
    steps = {s['key']: s['state'] for s in rep['steps']}
    assert steps['camel-3-to-4'] == 'APPLIED' and steps['camel4-compat-mapping'] == 'APPLIED'          # 24: two phases, two snapshots
    ops = {(m['op'], m['artifact']) for m in rep['pomMapping']}
    assert ('rename', 'camel-swagger-java') in ops and ('align-version', 'camel-jackson') in ops
    pom = (out / 'migrated-project/orders-routes/pom.xml').read_text()
    assert '2.24.3' not in pom and 'camel-swagger-java' not in pom                                      # 22/25: no version mix, no blind bump
    assert pom.count('<artifactId>camel-openapi-java</artifactId>') == 1 and pom.count('<artifactId>camel-http-starter</artifactId>') <= 1
    norm = [c['change'] for n in rep.get('pomNormalization', []) for c in n['changes']]
    assert any('camel-http' in c and 'duplicado' in c for c in norm)                                    # 23: deduplicated
    assert '${project.artifactId}' in pom and '${artifactId}' not in pom                               # 23: SAFE_AUTOFIX
    assert [g['status'] for g in rep['preflightGates']] == ['PASS'] * 5 or all(g['status'] in ('PASS', 'WARN') for g in rep['preflightGates'])
    assert any(n.startswith('candidate-compile') for n, _ in fake.calls)                                # compiled only after the gates
    run = (out / 'reports/migration-report.md').read_text()
    assert 'Preflight gates' in run and 'CAMEL_VERSION_CONSISTENCY_GATE: PASS' in run


def test_version_conflict_stops_before_compile_with_root_cause(tmp_path):                         # 22, 29, 30
    pom = ('<project><groupId>g</groupId><artifactId>m</artifactId><version>1</version><dependencies>'
           '<dependency><groupId>org.apache.camel</groupId><artifactId>camel-core</artifactId><version>4.14.0</version></dependency>'
           '<dependency><groupId>org.apache.camel</groupId><artifactId>camel-cxf-soap</artifactId><version>2.23.2</version></dependency>'
           '</dependencies></project>')
    (tmp_path / 'pom.xml').write_text(pom)
    r = preflight_gates.run(tmp_path, '4.14.0', usage={}, remote_check=lambda *a: ('EXISTS', 'x'))
    assert r['primary'] == 'CAMEL_VERSION_CONFLICT' and r['gates'][2]['conflicts'] == [{'artifact': 'camel-cxf-soap', 'version': '2.23.2', 'file': 'pom.xml'}]
    assert [g['status'] for g in r['gates'][3:]] == ['SKIPPED', 'SKIPPED']                                # not FAILED
    assert r['rootCause']['artifact'] == 'camel-cxf-soap' and 'Camel 4.14.0' in r['rootCause']['recommendedAction']


def test_osgi_bundle_requires_a_runtime_migration(tmp_path, monkeypatch):                          # 27
    install(monkeypatch, FakeMaven(effective_ok=True))
    src = phygital(tmp_path, packaging='bundle')
    report = cli_tool.analyze(src, 'spring', None, False, lambda m: None)
    assert report['runtime'] == 'osgi-blueprint' and report['runtimeMigration']['status'] == 'ARCHITECTURAL_MIGRATION'
    assert report['runtimeMigration']['targetRuntime'] == 'SPRING_BOOT' and 'packaging=bundle' in report['runtimeMigration']['signals']
    assert any(f['ruleId'] == 'TARGET_RUNTIME_MIGRATION_REQUIRED' for f in report['findings'])
    plan = cli_tool.build_plan(report, 'spring', '21', {'CAMEL2_VERSION': 'aceptado en la prueba'})
    step = next(s for s in plan['steps'] if s['key'] == 'manual-target-runtime-migration-required')
    assert step['state'] == 'blocked' and any('bundle → jar' in n for n in step['notes'])


def test_compile_errors_are_structured_and_a_worse_round_is_rolled_back(tmp_path, monkeypatch):   # 28, 30
    fake = install(monkeypatch, FakeMaven(effective_ok=True))
    calls = {'n': 0}
    orig = fake.mvn
    err1 = ('[ERROR] COMPILATION ERROR :\n[ERROR] /w/.work/candidate/orders-routes/src/main/java/demo/orders/A.java:[42,17] cannot find symbol\n'
            '[ERROR]   symbol:   class SwaggerDefinition\nNoSuchEndpointException: No endpoint could be found for: direct://process-order\n')
    err3 = err1 + ''.join(f'[ERROR] /w/.work/candidate/orders-routes/src/main/java/demo/orders/B.java:[{i},1] incompatible types\n' for i in (1, 2))

    def mvn(ctx, cwd, goals, name, timeout=None):
        if name.startswith('candidate-compile-'):
            calls['n'] += 1
            return fake._result(ctx, name, 1, err1 if calls['n'] == 1 else err3)
        return orig(ctx, cwd, goals, name, timeout)
    orig_rw = fake.rewrite

    def rewrite(ctx, repo, goal, **kw):
        if kw['name'].startswith('autofix-'):
            pom = Path(repo) / 'orders-routes' / 'pom.xml'
            pom.write_text(pom.read_text().replace('</dependencies>', '<!-- autofix --></dependencies>'))
            return fake._result(ctx, kw['name'], 0, 'ok')
        return orig_rw(ctx, repo, goal, **kw)
    monkeypatch.setattr('mf.worker.mavenops.mvn', mvn)
    monkeypatch.setattr('mf.worker.mavenops.rewrite', rewrite)
    out = tmp_path / 'out'
    assert cli_tool.main(['migrate', str(PILOT), '--output', str(out), '--accept', ACCEPT, '--skip-baseline']) == 2
    rep = json.loads((out / 'reports/migration-report.json').read_text())
    assert rep['autofix'][0]['rolledBack'] and rep['autofixStopped'].startswith('AUTOFIX_ROLLED_BACK')     # errors 1 -> 3: rollback, stop
    assert '<!-- autofix -->' not in (out / 'migrated-project/orders-routes/pom.xml').read_text()
    assert rep['compileErrors']['count'] == 3 and rep['compileErrors']['byType'] == {'CANNOT_FIND_SYMBOL': 1, 'INCOMPATIBLE_TYPES': 2}
    assert rep['primaryReason'] == 'JAVA_COMPILATION_ERROR' and 'CANDIDATE_COMPILE_FAILED' in rep['reasonCodes']   # 30: technical cause first
    assert rep['rootCause']['file'] == 'orders-routes/src/main/java/demo/orders/A.java:42' and rep['rootCause']['symbol'] == 'SwaggerDefinition'
    assert 'Errores Java (estructurados)' in (out / 'reports/migration-report.md').read_text()


def test_unknown_error_is_not_confused_with_no_parser():                                         # 28
    assert compile_errors.summary('[ERROR] /p/src/main/java/A.java:[1,1] some new message', True)['errors'][0]['errorType'] == 'UNKNOWN_JAVA_ERROR'
    assert compile_errors.summary('[ERROR] COMPILATION ERROR :\n[ERROR] garbled', True)['parser'] == 'UNPARSED_BUILD_FAILURE'

"""Pre-flight / baseline repair (camel-migration-tool-docs 2, doc 14)."""
import json
from pathlib import Path

import pytest

from helpers import FakeMaven, install
from mf import cli_tool, preflight
from mf.analysis.scanner import scan

LEGACY_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>demo</groupId>
  <artifactId>versionless</artifactId>
  <version>1.0</version>
  <packaging>war</packaging>
  <properties>
    <camel.version>2.24.3</camel.version>
    <maven.compiler.source>1.8</maven.compiler.source>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.apache.camel</groupId>
      <artifactId>camel-cdi</artifactId>
    </dependency>
    <dependency>
      <groupId>org.apache.camel</groupId>
      <artifactId>camel-http4</artifactId>
    </dependency>
    <dependency>
      <groupId>org.apache.camel</groupId>
      <artifactId>camel-core</artifactId>
      <version>${camel.version}</version>
    </dependency>
  </dependencies>
</project>
"""


@pytest.fixture
def legacy(tmp_path):
    p = tmp_path / 'legacy'
    (p / 'src/main/java/demo').mkdir(parents=True)
    (p / 'pom.xml').write_text(LEGACY_POM)
    (p / 'src/main/java/demo/R.java').write_text('package demo;\nimport org.apache.camel.builder.RouteBuilder;\n'
                                                 'public class R extends RouteBuilder { public void configure() { from("direct:a").routeId("a").to("http4://h/x"); } }\n')
    return p


def test_model_validation_and_bom_repair(legacy):
    report = scan(legacy)
    model = preflight.validate_model(legacy, report['modules'], report['errors'])
    assert {f['artifact'] for f in model} == {'org.apache.camel:camel-cdi', 'org.apache.camel:camel-http4'}
    assert all(f['category'] == 'BUILD_MODEL' for f in model)
    repairs = preflight.repair(legacy, report['modules'], model, exists=lambda g, a, v: (g, a, v) == ('org.apache.camel', 'camel-bom', '2.24.3'))
    assert len(repairs) == 1 and 'camel-bom:2.24.3' in repairs[0]['change'] and repairs[0]['before'] != repairs[0]['after']
    pom = (legacy / 'pom.xml').read_text()
    assert '<artifactId>camel-bom</artifactId>' in pom and '<scope>import</scope>' in pom
    assert '<artifactId>camel-http4</artifactId>' in pom and 'camel-http<' not in pom  # repair never migrates components
    assert all(f['baseline'] == 'FIXED' and f['autoFix'] for f in model)
    assert scan(legacy)['modules'][0]['unresolved']  # BOM is external: static model still cannot resolve it (Maven will)


def test_repair_requires_evidence(legacy):
    report = scan(legacy)
    model = preflight.validate_model(legacy, report['modules'], report['errors'])
    assert preflight.repair(legacy, report['modules'], model, exists=lambda *a: False) == []
    assert 'no verificable' in model[0]['fix']
    (legacy / 'pom.xml').write_text(LEGACY_POM.replace('<camel.version>2.24.3</camel.version>', '').replace('${camel.version}', '2.24.3'))
    report = scan(legacy)
    model = preflight.validate_model(legacy, report['modules'], report['errors'])
    assert len(preflight.repair(legacy, report['modules'], model, exists=lambda *a: True)) == 1  # version from camel-core
    assert '<camel.version>2.24.3</camel.version>' in (legacy / 'pom.xml').read_text()


def test_classification_and_states():
    log = ("[ERROR] 'dependencies.dependency.version' for org.apache.camel:camel-cdi:jar is missing. @ line 37\n"
           "Failed to load native library: jansi-2.4.0.so /tmp/x is not executable\n"
           "[ERROR] /p/src/main/java/A.java:[3,17] cannot find symbol\n[ERROR] There are test failures.\n")
    cats = [e['category'] for e in preflight.classify(log)]
    assert cats == ['BUILD_MODEL', 'ENVIRONMENT', 'SOURCE', 'TEST']
    assert preflight.state(True, [], []) == 'BASELINE_SUCCESS'
    assert preflight.state(True, [{'x': 1}], []) == 'BASELINE_REPAIRED'
    assert preflight.state(False, [], [{'category': 'TEST'}]) == 'BASELINE_FAILED_TEST'
    assert preflight.state(False, [], [{'category': 'SOURCE'}]) == 'BASELINE_FAILED_CODE'
    assert preflight.state(False, [], [{'category': 'BUILD_MODEL'}]) == 'BASELINE_BLOCKED'
    fp = lambda s: preflight.classify(s)[0]['fingerprint']
    assert fp('[ERROR] /a/b/src/main/java/A.java:[3,17] cannot find symbol') == fp('[ERROR] /c/src/main/java/A.java:[4,1] cannot find symbol')


def test_cli_preflight_and_migrate_gate(legacy, tmp_path, monkeypatch):
    fake = install(monkeypatch, FakeMaven(effective_ok=True, model_ok=lambda cwd: 'camel-bom' in (Path(cwd) / 'pom.xml').read_text()))
    monkeypatch.setattr(preflight, '_artifact_exists', lambda *a: True)
    out = tmp_path / 'pf'
    assert cli_tool.main(['preflight', str(legacy), '--output', str(out)]) == 0
    data = json.loads((out / 'analysis.json').read_text())
    assert data['baseline']['status'] == 'BASELINE_REPAIRED' and data['baseline']['repairs'][0]['kind'] == 'import-camel-bom'
    assert '## Reparaciones aplicadas' in (out / 'baseline-report.md').read_text()
    assert 'camel-bom' not in (legacy / 'pom.xml').read_text()  # the source is never repaired, only the copy

    # baseline build failing for an unknown/model reason -> BLOCKED -> no migration
    orig = fake.mvn
    monkeypatch.setattr('mf.worker.mavenops.mvn', lambda ctx, cwd, goals, name, timeout=None:
                        fake._result(ctx, name, 1, "[ERROR] 'dependencies.dependency.version' for x:y:jar is missing.")
                        if name == 'baseline-compile' else orig(ctx, cwd, goals, name, timeout))
    o2 = tmp_path / 'm'
    assert cli_tool.main(['migrate', str(legacy), '--output', str(o2), '--accept', 'CAMEL2_VERSION=revisado manualmente en la prueba']) == 1
    assert not (o2 / 'migrated-project').exists() and 'BLOCKED' in (o2 / 'reports' / 'baseline-report.md').read_text()

    # failing tests in the legacy -> FAILED_KNOWN -> needs explicit --allow-baseline-failure
    monkeypatch.setattr('mf.worker.mavenops.mvn', lambda ctx, cwd, goals, name, timeout=None:
                        fake._result(ctx, name, 1, '[ERROR] There are test failures.') if name == 'baseline-compile' else orig(ctx, cwd, goals, name, timeout))
    assert cli_tool.main(['migrate', str(legacy), '--output', str(tmp_path / 'm2'), '--accept', 'CAMEL2_VERSION=revisado manualmente en la prueba']) == 1
    code = cli_tool.main(['migrate', str(legacy), '--output', str(tmp_path / 'm3'), '--accept', 'CAMEL2_VERSION=revisado manualmente en la prueba',
                          '--allow-baseline-failure'])
    assert code == 0, (tmp_path / 'm3' / 'reports' / 'migration-report.md').read_text()
    rep = (tmp_path / 'm3' / 'reports' / 'migration-report.md').read_text()
    assert 'Baseline state: BASELINE_FAILED_TEST' in rep and 'MAVEN-CAMEL-MISSING-VERSION' in rep
    steps = {x['key']: x['state'] for x in json.loads((tmp_path / 'm3' / 'reports' / 'migration-report.json').read_text())['steps']}
    assert steps.get('runtime-spring') == 'APPLIED', steps  # resolution after repair must not block the runtime step
    patch = (tmp_path / 'm3' / 'reports' / 'candidate.patch').read_text()
    assert 'camel-bom' not in patch and 'camel-bom' in (tmp_path / 'm3' / 'reports' / 'baseline-repair.patch').read_text()  # repair and migration reported apart


def test_existing_camel_bom_is_not_duplicated(legacy):
    pom = LEGACY_POM.replace('  <dependencies>\n', '  <dependencyManagement><dependencies><dependency><groupId>org.apache.camel</groupId>'
                             '<artifactId>camel-bom</artifactId><version>${camel.version}</version><type>pom</type><scope>import</scope>'
                             '</dependency></dependencies></dependencyManagement>\n  <dependencies>\n', 1)
    (legacy / 'pom.xml').write_text(pom)
    report = scan(legacy)
    model = preflight.validate_model(legacy, report['modules'], report['errors'])
    assert all(f['severity'] == 'INFO' for f in model if f.get('artifact'))
    assert preflight.repair(legacy, report['modules'], model, exists=lambda *a: True) == []
    assert (legacy / 'pom.xml').read_text().count('camel-bom') == 1

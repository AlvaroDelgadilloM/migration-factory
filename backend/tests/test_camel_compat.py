"""19-CORRECCION-CAMEL-COMPATIBILITY part A: registry, artifact validation and DEPENDENCY_VALIDATION_GATE (Maven faked)."""
import json
import shutil

import pytest

from conftest import ROOT
from helpers import FakeMaven, install
from mf import cli_tool

PILOT = ROOT / 'examples' / 'pilot-orders'
ACCEPT = 'CAMEL2_VERSION=Revisado contra la guía 2→3 en la fixture'


def with_dependency(base, artifact, extra_java=''):
    p = base / 'legacy'
    shutil.copytree(PILOT, p)
    pom = p / 'orders-routes' / 'pom.xml'
    pom.write_text(pom.read_text().replace('</dependencies>', f'<dependency><groupId>org.apache.camel</groupId><artifactId>{artifact}</artifactId>'
                                                              '<version>${camel.version}</version></dependency></dependencies>', 1))
    if extra_java:
        (p / 'orders-routes/src/main/java/demo/orders/ApiDocs.java').write_text(extra_java)
    return p


REST = ('package demo.orders;\nimport org.apache.camel.builder.RouteBuilder;\npublic class ApiDocs extends RouteBuilder {\n'
        '  public void configure() { restConfiguration().component("servlet").apiContextPath("/api-doc"); }\n}\n')


def test_swagger_from_rest_dsl_is_replaced_never_version_bumped(tmp_path, monkeypatch):           # case 1
    install(monkeypatch, FakeMaven(effective_ok=True))
    out = tmp_path / 'out'
    assert cli_tool.main(['migrate', str(with_dependency(tmp_path, 'camel-swagger-java', REST)), '--output', str(out), '--accept', ACCEPT,
                          '--skip-baseline']) == 0
    pom = (out / 'migrated-project/orders-routes/pom.xml').read_text()
    assert 'camel-swagger-java' not in pom and 'camel-openapi-java' in pom
    rep = json.loads((out / 'reports/migration-report.json').read_text())
    sw = next(c for c in rep['camelComponents'] if c['artifact'] == 'camel-swagger-java')
    assert sw['status'] == 'REPLACED' and sw['action'] == 'RENAME' and sw['target'] == 'camel-openapi-java'
    assert all(g['status'] in ('PASS', 'WARN') for g in rep['preflightGates'])
    md = (out / 'reports/camel-component-migration-report.md').read_text()
    assert 'Source dependency:\ncamel-swagger-java' in md and 'Code usage found' not in md   # doc 36: no generic signal list
    block = md.split('## camel-swagger-java')[1].split('```\n\n')[0]
    assert 'type=CONFIGURATION value=apiContextPath file=orders-routes/src/main/java/demo/orders/ApiDocs.java line=4' in block
    assert 'Usage confidence:\nHIGH' in block and 'Artifact confidence:\nHIGH' in block


def test_swagger_without_rest_dsl_usage_is_not_replaced_blindly(tmp_path, monkeypatch):
    install(monkeypatch, FakeMaven(effective_ok=True))
    out = tmp_path / 'out'
    assert cli_tool.main(['migrate', str(with_dependency(tmp_path, 'camel-swagger-java')), '--output', str(out), '--accept', ACCEPT,
                          '--skip-baseline']) == 2
    rep = json.loads((out / 'reports/migration-report.json').read_text())
    assert rep['primaryReason'] == 'ARTIFACT_REPLACEMENT_REQUIRED' and rep['status'] == 'FAILED'


def test_blueprint_is_an_architectural_migration(tmp_path, monkeypatch):                          # case 2
    install(monkeypatch, FakeMaven(effective_ok=True))
    src = with_dependency(tmp_path, 'camel-blueprint')
    bp = src / 'orders-routes/src/main/resources/OSGI-INF/blueprint/camel.xml'
    bp.parent.mkdir(parents=True)
    bp.write_text('<blueprint xmlns="http://www.osgi.org/xmlns/blueprint/v1.0.0"><camelContext xmlns="http://camel.apache.org/schema/blueprint"/></blueprint>\n')
    out = tmp_path / 'out'
    assert cli_tool.main(['migrate', str(src), '--output', str(out), '--accept', ACCEPT, '--skip-baseline']) == 2
    rep = json.loads((out / 'reports/migration-report.json').read_text())
    assert rep['primaryReason'] == 'ARCHITECTURAL_MIGRATION_REQUIRED'
    assert next(s for s in rep['steps'] if s['key'] == 'camel-3-to-4')['state'] == 'SKIPPED'   # no version bump of camel-blueprint
    analysis = json.loads((out / 'reports/analysis.json').read_text())
    assert any(f['ruleId'] == 'BLUEPRINT' for f in analysis['findings'])


def test_invalid_artifact_stops_before_the_build(tmp_path, monkeypatch):                          # case 3
    fake = install(monkeypatch, FakeMaven(effective_ok=True))
    out = tmp_path / 'out'
    code = cli_tool.main(['migrate', str(with_dependency(tmp_path, 'camel-xmljson')), '--output', str(out), '--accept', ACCEPT,
                          '--accept', 'CAMEL_COMPONENT_COMPAT=Decisión del arquitecto: se reemplazará a mano', '--skip-baseline'])
    rep = json.loads((out / 'reports/migration-report.json').read_text())
    assert code == 2 and rep['primaryReason'] == 'INVALID_CAMEL_COMPONENT_MAPPING'          # doc 29: first failing gate is the root cause
    gates = {g['name']: g['status'] for g in rep['preflightGates']}
    assert gates == {'POM_SANITY_GATE': 'PASS', 'CAMEL_COMPONENT_COMPATIBILITY_GATE': 'FAIL', 'CAMEL_VERSION_CONSISTENCY_GATE': 'SKIPPED',
                     'ARTIFACT_AVAILABILITY_GATE': 'SKIPPED', 'DEPENDENCY_VALIDATION_GATE': 'SKIPPED'}
    assert rep['rootCause']['artifact'] == 'camel-xmljson' and rep['rootCause']['gate'] == 'CAMEL_COMPONENT_COMPATIBILITY_GATE'
    compile_ = [v for v in rep['validations'] if v.get('name') == 'compile']
    assert compile_ == [{'target': 'candidate', 'stage': 'CANDIDATE', 'name': 'compile', 'status': 'SKIPPED',
                         'reason': 'BLOCKED_BY_PREFLIGHT_GATE: INVALID_CAMEL_COMPONENT_MAPPING'}]
    assert not any(n.startswith('candidate-compile') for n, _ in fake.calls)  # Maven never asked to build it

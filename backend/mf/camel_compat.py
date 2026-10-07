"""CamelComponentCompatibilityRegistry v2 (19-CORRECCION-CAMEL-COMPATIBILITY part A; camel-cxf-evidence-corrections 33-40).

No generic "same artifact, version 4.x": every org.apache.camel dependency is classified (SUPPORTED, RENAMED, REPLACED,
REMOVED, ARCHITECTURAL_MIGRATION, MANUAL_REVIEW), its target is checked against the versioned target BOM snapshot, and
how the component is used decides automatic replacements. Anything else blocks the Camel step with an explicit code.
Stdlib only."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import rules as mrules

RULES = Path(__file__).resolve().parents[2] / 'rules' / 'camel'
CODE = {'REMOVED': 'REMOVED_CAMEL_COMPONENT', 'ARCHITECTURAL_MIGRATION': 'ARCHITECTURAL_MIGRATION_REQUIRED',
        'MANUAL_REVIEW': 'INVALID_CAMEL_COMPONENT_MAPPING', 'REPLACED': 'ARTIFACT_REPLACEMENT_REQUIRED'}
CODES = ('INVALID_CAMEL_COMPONENT_MAPPING', 'UNSUPPORTED_TARGET_ARTIFACT', 'REMOVED_CAMEL_COMPONENT', 'ARTIFACT_REPLACEMENT_REQUIRED',
         'ARCHITECTURAL_MIGRATION_REQUIRED', 'CXF_USAGE_AMBIGUOUS')


@lru_cache
def registry() -> dict:
    data = json.loads((RULES / 'camel-3-to-4-components.json').read_text(encoding='utf-8'))
    for a, e in data['components'].items():
        assert e['status'] in data['statuses'], a
        assert e['status'] != 'REPLACED' or e.get('target'), a
        assert e['status'] != 'ARCHITECTURAL_MAPPING' or e.get('resolver'), a
    return data


@lru_cache
def bom(coordinate: str) -> frozenset | None:
    """Managed g:a of a BOM from its verified snapshot (rules/camel/boms), None if not snapshotted."""
    g, a, v = coordinate.split(':')
    p = RULES / 'boms' / f'{a}-{v}.json'
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding='utf-8'))
    assert data['coordinate'] == coordinate
    return frozenset(data['artifacts'])


def _usage_ok(spec, usage) -> bool:
    return any(usage.get(t) for t in spec.get('any', [])) and not any(usage.get(t) for t in spec.get('none', []))


# --- evidence (doc 34) ------------------------------------------------------------------------------------------------
CORE_SCHEMES = {'direct', 'seda', 'vm', 'direct-vm', 'log', 'bean', 'timer', 'mock', 'file', 'language', 'ref', 'class', 'controlbus', 'stub'}
SOAP = ('uri:cxf', 'cxf:bean:', 'cxf://', 'cxf:cxfEndpoint', 'javax.jws', 'jakarta.jws', '@WebService', 'JaxWsProxyFactoryBean', 'JaxWsServerFactoryBean')
REST = ('uri:cxfrs', 'cxfrs:', 'cxf:rsServer', 'cxf:rsClient', 'javax.ws.rs', 'jakarta.ws.rs', 'JAXRSServerFactoryBean', 'JAXRSClientFactoryBean')
BLUEPRINT = ('OSGI-INF/blueprint', 'osgi.org/xmlns/blueprint')
SPRING_XML = 'springframework.org/schema/beans'


def evidence_index(usage: dict, evidence: dict | None = None, endpoints=()) -> dict:
    """token -> [{file, line}]: scanner evidence (with line), else the legacy token -> files, plus route endpoints as uri:<scheme>."""
    idx = {t: list(v) for t, v in (evidence or {}).items()}
    for t, files in (usage or {}).items():
        idx.setdefault(t, [{'file': f, 'line': None} for f in files])
    for e in endpoints or ():
        if e.get('component'):
            idx.setdefault(f"uri:{e['component']}", []).append({'file': e['file'], 'line': e.get('line')})
    return idx


def _etype(token: str) -> str:
    if token.startswith('uri:') or token in ('cxf:bean:', 'cxf://', 'cxfrs:', 'servlet:', 'rest-swagger:'):
        return 'CAMEL_URI_SCHEME'
    if token.startswith('@'):
        return 'JAVA_ANNOTATION'
    if token.startswith(('javax.', 'jakarta.', 'org.')):
        return 'JAVA_PACKAGE'
    if token.startswith(('cxf:', '<')) or '/' in token or token in ('restConfiguration', 'apiContextPath', 'apiProperty'):
        return 'CONFIGURATION'
    return 'JAVA_API'


def dependency_evidence(artifact: str, idx: dict) -> list[dict]:
    """Component-specific evidence only: never the project's signal list copied onto every dependency."""
    e = registry()['components'].get(artifact, {})
    scheme = artifact.removeprefix('camel-')
    tokens = e.get('evidence') or ([f'uri:{s}' for s in sorted(CORE_SCHEMES)] if artifact == 'camel-core' else [f'uri:{scheme}', f'{scheme}:'])
    out = []
    for t in tokens:
        for loc in idx.get(t, [])[:5]:
            out.append({'type': _etype(t), 'value': t.removeprefix('uri:') + (':' if t.startswith('uri:') else ''), 'file': loc['file'],
                        'line': loc.get('line'), 'source': 'static-scan'})
    seen, uniq = set(), []
    for x in out:
        k = (x['value'], x['file'], x['line'])
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq[:10]


def project_context(idx: dict, packagings=(), platform: str | None = None, tests: int | None = None) -> dict:
    """ProjectEvidence: global context, reported once per dependency but never presented as its evidence."""
    blueprint = any(idx.get(t) for t in BLUEPRINT)
    bundle = 'bundle' in packagings
    runtime = (platform or 'OSGI_BLUEPRINT') if blueprint or bundle or platform else ('SPRING_XML' if idx.get(SPRING_XML) else 'JAVA_DSL')
    return {'packaging': sorted(set(packagings)), 'runtime': runtime, 'blueprint': blueprint, 'restDsl': bool(idx.get('restConfiguration')),
            'cxf': any(idx.get(t) for t in SOAP + REST), 'springXml': bool(idx.get(SPRING_XML)), 'tests': tests}


def cxf_usage(idx: dict, project: dict) -> dict:
    """CxfUsageAnalyzer (doc 33): SOAP/REST from endpoints, configuration and JAX-WS/JAX-RS APIs; configuration style from
    where the CXF endpoints are declared (Spring XML or Blueprint)."""
    soap = [t for t in SOAP if idx.get(t)]
    rest = [t for t in REST if idx.get(t)]
    files = {x['file'] for t in soap + rest for x in idx[t]}
    in_spring = files & {x['file'] for x in idx.get(SPRING_XML, [])}
    in_blueprint = files & {x['file'] for t in BLUEPRINT for x in idx.get(t, [])}
    signals = (['SOAP'] if soap else []) + (['REST'] if rest else []) + (['SPRING_XML'] if in_spring else []) \
        + (['BLUEPRINT'] if in_blueprint or project.get('blueprint') else []) \
        + (['JAVA_DSL'] if any(f.endswith('.java') for t in ('uri:cxf', 'uri:cxfrs', 'cxf:bean:', 'cxfrs:') for f in (x['file'] for x in idx.get(t, []))) else []) \
        + (['JAX_WS'] if any(idx.get(t) for t in ('javax.jws', 'jakarta.jws', '@WebService', 'JaxWsProxyFactoryBean', 'JaxWsServerFactoryBean')) else []) \
        + (['JAX_RS'] if any(idx.get(t) for t in ('javax.ws.rs', 'jakarta.ws.rs', 'JAXRSServerFactoryBean', 'JAXRSClientFactoryBean')) else []) \
        + (['TRANSPORT'] if idx.get('CXFServlet') else [])
    usage = 'SOAP_AND_REST' if soap and rest else 'SOAP' if soap else 'REST' if rest else 'UNKNOWN'
    return {'usage': usage, 'signals': signals or ['UNKNOWN'], 'config': 'spring_xml' if in_spring else 'plain', 'blueprint': 'BLUEPRINT' in signals}


# --- confidence (doc 35): levels with factors, never a numeric score ------------------------------------------------
def confidences(d: dict, dep_evidence: list, project: dict, verified: bool | None) -> dict:
    artifact = 'N/A' if verified is None else ('HIGH' if verified else 'LOW')
    usage = 'HIGH' if dep_evidence else 'LOW'
    factors = {'targetIdentified': bool(d.get('target')) or d['action'] in ('KEEP', 'REMOVE'),
               'artifactVerified': verified is not False,
               'runtimeCompatible': project.get('runtime') in ('SPRING_XML', 'JAVA_DSL'),
               'directEvidence': bool(dep_evidence),
               'validatable': bool(project.get('tests'))}
    if d['action'] == 'BLOCK' or not factors['targetIdentified'] or not factors['artifactVerified']:
        migration = 'LOW'
    elif all(factors.values()) and d.get('autoMigration') == 'YES':
        migration = 'HIGH'
    else:
        migration = 'MEDIUM'
    return {'artifactConfidence': artifact, 'usageConfidence': usage, 'migrationConfidence': migration, 'confidence': migration,
            'confidenceFactors': factors}


def decide(artifact: str, usage: dict, target_camel: str, *, evidence: dict | None = None, endpoints=(), packagings=(),
           platform: str | None = None, tests: int | None = None) -> dict:
    """artifact: org.apache.camel artifactId; usage: token -> files (scanner); target_camel: version the Camel recipe targets.
    evidence/endpoints/packagings/platform/tests: optional context (doc 34-35); without them the decision is the same, with
    less evidence (and therefore lower usage/migration confidence)."""
    idx = evidence_index(usage, evidence, endpoints)
    project = project_context(idx, packagings, platform, tests)
    dep_ev = dependency_evidence(artifact, idx)
    d, verified = _decide(artifact, usage, idx, project, target_camel)
    d |= {'projectContext': project, 'dependencyEvidence': dep_ev,
          'runtimeMigration': 'REQUIRED' if project['runtime'] not in ('SPRING_XML', 'JAVA_DSL') or d['status'] == 'ARCHITECTURAL_MIGRATION' else 'NOT_REQUIRED'}
    if d['runtimeMigration'] == 'REQUIRED' and d.get('autoMigration') == 'YES' and d['action'] == 'RENAME':
        d['autoMigration'] = 'CONDITIONAL'  # doc 33: with Blueprint/OSGi the dependency change alone is never "fully automatic"
    return d | confidences(d, dep_ev, project, verified)


def _decide(artifact, usage, idx, project, target_camel):
    target_bom = bom(f'org.apache.camel:camel-bom:{target_camel}') or frozenset()
    exists = lambda a: f'org.apache.camel:{a}' in target_bom
    found = sorted(t for t in registry()['usageTokens'] if usage.get(t))
    base = {'artifact': artifact, 'targetCamel': target_camel, 'usageFound': found, 'alternatives': []}
    renamed = mrules.renamed()
    e = registry()['components'].get(artifact)
    if e and e.get('resolver') == 'CxfUsageAnalyzer':
        cx = cxf_usage(idx, project)
        possible = sorted({t for kinds in e['targets'].values() for t in kinds.values()})
        out = base | {'status': e['status'], 'targetStatus': 'DETECT_BY_USAGE', 'possibleTargets': possible, 'detectedUsage': cx['usage'],
                      'cxfSignals': cx['signals']}
        if cx['usage'] == 'UNKNOWN':
            return out | {'action': 'BLOCK', 'code': 'CXF_USAGE_AMBIGUOUS', 'resolution': 'MANUAL_REVIEW', 'autoMigration': 'NO',
                          'reason': 'No hay evidencia de uso SOAP ni REST de CXF: revisión manual (nunca se elige camel-cxf-soap solo por el artifactId).'}, None
        if cx['usage'] == 'SOAP_AND_REST':
            targets = [e['targets'][k][cx['config']] for k in ('SOAP', 'REST')]
            return out | {'action': 'BLOCK', 'code': 'ARTIFACT_REPLACEMENT_REQUIRED', 'targets': targets, 'autoMigration': 'NO',
                          'reason': f"Usa CXF SOAP y REST: requiere {' y '.join(targets)}; decidir por módulo."}, all(exists(t) for t in targets)
        t = e['targets'][cx['usage']][cx['config']]
        if not exists(t):
            return out | {'action': 'BLOCK', 'code': 'UNSUPPORTED_TARGET_ARTIFACT', 'target': t, 'autoMigration': 'NO',
                          'reason': f'{t} no está en camel-bom {target_camel}.'}, False
        how = 'Spring XML' if cx['config'] == 'spring_xml' else 'configuración plana/Java'
        return out | {'action': 'RENAME', 'target': t, 'autoMigration': 'CONDITIONAL',
                      'reason': f"Uso {cx['usage']} detectado ({', '.join(cx['signals'])}) con {how} → {t}. El contrato se verifica con pruebas."}, True
    if artifact in renamed:
        t = renamed[artifact]
        if not exists(t):
            return base | {'status': 'RENAMED', 'action': 'BLOCK', 'code': 'UNSUPPORTED_TARGET_ARTIFACT', 'target': t, 'autoMigration': 'NO',
                           'reason': f'{t} no está en camel-bom {target_camel}.'}, False
        rule = mrules.rule_for(artifact)
        return base | {'status': 'RENAMED', 'action': 'RENAME', 'target': t, 'autoMigration': 'YES',
                       'rule': rule['id'] if rule else None, 'reason': (rule or {}).get('rationale', f'{artifact} → {t}')}, True
    if e:
        alts = [{'name': a, 'verified': exists(a)} for a in e.get('alternatives', [])]
        out = base | {'status': e['status'], 'reason': e['reason'], 'alternatives': alts, 'target': e.get('target')}
        out |= {k: e[k] for k in ('runtimeOptions', 'actions', 'sourceCompatibility', 'sourceMigration', 'prerequisites', 'runtimeConfigMigration') if k in e}
        if e['status'] == 'SUPPORTED':  # doc 38: the artifact exists; source/runtime compatibility is reported separately
            t = e.get('artifactTarget', artifact)
            if not exists(t):
                return out | {'action': 'BLOCK', 'code': 'UNSUPPORTED_TARGET_ARTIFACT', 'target': t, 'autoMigration': 'NO'}, False
            return out | {'action': 'KEEP', 'target': t, 'autoMigration': 'YES',
                          'sourceCompatibility': e.get('sourceCompatibility', 'REQUIRES_VALIDATION')}, True
        if e['status'] == 'REPLACED':
            if not exists(e['target']):
                return out | {'action': 'BLOCK', 'code': 'UNSUPPORTED_TARGET_ARTIFACT', 'autoMigration': 'NO'}, False
            if e.get('auto') and _usage_ok(e.get('usage', {}), usage):
                return out | {'action': 'RENAME', 'autoMigration': 'YES'}, True
            why = '' if e.get('auto') else ' (reemplazo con cambios de configuración)'
            return out | {'action': 'BLOCK', 'code': 'ARTIFACT_REPLACEMENT_REQUIRED', 'autoMigration': 'NO',
                          'reason': e['reason'] + why + (f" Uso encontrado: {', '.join(found) or 'ninguno'}." if e.get('usage') else '')}, True
        if e['status'] == 'ARCHITECTURAL_MIGRATION':
            out |= {'target': None, 'artifactTarget': 'N/A'}
        unused = e.get('ifUnused')
        if unused and not any(usage.get(t) for t in unused['tokens']):
            return out | {'action': 'REMOVE', 'autoMigration': 'YES', 'reason': e['reason'] + ' Sin uso en el código: solo se elimina la dependencia.'}, None
        return out | {'action': 'BLOCK', 'code': CODE[e['status']], 'autoMigration': 'NO'}, None
    if exists(artifact):
        return base | {'status': 'SUPPORTED', 'action': 'KEEP', 'autoMigration': 'YES', 'sourceCompatibility': 'REQUIRES_VALIDATION',
                       'reason': f'Presente en camel-bom {target_camel}.'}, True
    return base | {'status': 'MANUAL_REVIEW', 'action': 'BLOCK', 'code': 'UNSUPPORTED_TARGET_ARTIFACT', 'autoMigration': 'NO',
                   'reason': f'org.apache.camel:{artifact} no existe en camel-bom {target_camel} y no está en el registro: '
                             'no se escribe con versión Camel 4; elegir reemplazo con evidencia.'}, False


def report_markdown(decisions) -> str:
    """Doc 36 format: project context and dependency evidence are separate; three confidence levels."""
    out = ['# Migración de componentes Camel', '',
           'Cada dependencia org.apache.camel se clasificó con el registro de compatibilidad v2 y su destino se verificó contra el BOM '
           'Camel destino antes de escribir el POM. La evidencia de cada dependencia es específica del componente; el contexto del '
           'proyecto se muestra aparte.', '']
    for d in decisions:
        pc = d.get('projectContext') or {}
        ev = d.get('dependencyEvidence')
        target = d.get('targetStatus') or d.get('artifactTarget') or d.get('target') or ', '.join(d.get('targets', [])) \
            or (d['artifact'] if d['action'] == 'KEEP' else '—')
        block = [f"## {d['artifact']}", '', '```text', 'Source dependency:', d['artifact'], '', 'Target status:', d['status'], '',
                 ('Artifact target:' if d['status'] == 'ARCHITECTURAL_MIGRATION' else 'Target:'), target]
        if d.get('targetStatus') and d.get('target'):
            block += ['', 'Resolved target:', d['target']]
        if d.get('possibleTargets'):
            block += ['', 'Possible targets:'] + [f'- {t}' for t in d['possibleTargets']]
        if d.get('runtimeOptions'):
            block += ['', 'Target runtime options:'] + [f'- {t}' for t in d['runtimeOptions']]
        if pc:
            block += ['', 'Project context:'] + [f"- packaging={','.join(pc['packaging']) or '?'}", f"- runtime={pc['runtime']}"]
        block += ['', 'Dependency evidence:']
        if ev:
            for x in ev:
                block += [f"- type={x['type']} value={x['value']} file={x['file']}" + (f" line={x['line']}" if x.get('line') else '')]
        else:
            block.append('NONE' if ev is not None else 'NO DISPONIBLE (análisis anterior al registro v2)')
        if d.get('detectedUsage'):
            block += ['', 'Detected usage:', d['detectedUsage'] + (f" ({', '.join(d.get('cxfSignals', []))})" if d.get('cxfSignals') else '')]
        for k, label in (('artifactConfidence', 'Artifact confidence'), ('usageConfidence', 'Usage confidence'),
                         ('migrationConfidence', 'Migration confidence')):
            if d.get(k):
                block += ['', f'{label}:', d[k]]
        auto = d.get('autoMigration')
        auto = {True: 'YES', False: 'NO'}.get(auto, auto) or ('YES' if d['action'] in ('KEEP', 'RENAME', 'REMOVE') else 'NO')
        block += ['', 'Auto dependency migration:', auto]
        if d.get('runtimeMigration'):
            block += ['', 'Runtime migration:', d['runtimeMigration']]
        for k, label in (('sourceCompatibility', 'Source compatibility'), ('sourceMigration', 'Source migration'),
                         ('runtimeConfigMigration', 'Runtime/config migration')):
            if d.get(k):
                block += ['', f'{label}:', d[k]]
        if d.get('prerequisites'):
            block += ['', 'Prerequisites:'] + [f'- {p}' for p in d['prerequisites']]
        if d.get('actions'):
            block += ['', 'Actions:'] + [f'- {a}' for a in d['actions']]
        if d.get('code'):
            block += ['', 'Reason code:', d['code']]
        alts = ', '.join(f"{a['name']}{'' if a['verified'] else ' (no verificado en el BOM)'}" for a in d.get('alternatives', []))
        block += ['', 'Reason:', d['reason']] + (['', 'Alternatives:', alts] if alts else []) + ['```', '']
        out += block
    return '\n'.join(out) + '\n'


if __name__ == '__main__':  # self-check: doc 19 part A + doc 40 regression cases
    rest = {'restConfiguration': ['R.java']}
    d = decide('camel-swagger-java', rest, '4.14.0')
    assert d['action'] == 'RENAME' and d['target'] == 'camel-openapi-java'
    d = decide('camel-swagger-java', rest | {'Swagger2Feature': ['C.java']}, '4.14.0')
    assert d['action'] == 'BLOCK' and d['code'] == 'ARTIFACT_REPLACEMENT_REQUIRED'      # never camel-swagger-java:4.x
    assert decide('camel-cdi', {}, '4.14.0')['action'] == 'REMOVE'
    assert decide('camel-cdi', {'org.apache.camel.cdi': ['A.java']}, '4.14.0')['code'] == 'ARCHITECTURAL_MIGRATION_REQUIRED'
    assert decide('camel-xmljson', {}, '4.14.0')['code'] == 'INVALID_CAMEL_COMPONENT_MAPPING'
    assert decide('camel-made-up', {}, '4.14.0')['code'] == 'UNSUPPORTED_TARGET_ARTIFACT'
    assert decide('camel-core', {}, '4.22.0')['action'] == 'KEEP'
    assert decide('camel-http4', {}, '4.14.0')['target'] == 'camel-http'
    # doc 40 test 1: SOAP Blueprint
    ev = {'OSGI-INF/blueprint': [{'file': 'src/main/resources/OSGI-INF/blueprint/routes.xml', 'line': None}],
          'cxf:bean:': [{'file': 'src/main/resources/OSGI-INF/blueprint/routes.xml', 'line': 42}]}
    d = decide('camel-cxf', {}, '4.14.0', evidence=ev, packagings=['bundle'])
    assert (d['status'], d['detectedUsage'], d['projectContext']['runtime'], d['autoMigration'], d['runtimeMigration']) == \
        ('ARCHITECTURAL_MAPPING', 'SOAP', 'OSGI_BLUEPRINT', 'CONDITIONAL', 'REQUIRED')
    assert d['dependencyEvidence'][0] == {'type': 'CAMEL_URI_SCHEME', 'value': 'cxf:bean:', 'file': ev['cxf:bean:'][0]['file'], 'line': 42, 'source': 'static-scan'}
    # test 2: REST in Spring XML
    ev = {'cxf:rsServer': [{'file': 'ctx.xml', 'line': 3}], 'springframework.org/schema/beans': [{'file': 'ctx.xml', 'line': 2}]}
    assert decide('camel-cxf', {}, '4.14.0', evidence=ev)['target'] == 'camel-cxf-spring-rest'
    assert decide('camel-cxf', {'cxfrs:': ['x.java']}, '4.14.0')['target'] == 'camel-cxf-rest'
    assert decide('camel-cxf', {'cxfrs:': ['x'], 'cxf:bean:': ['y']}, '4.14.0')['action'] == 'BLOCK'
    # test 3: evidence isolation
    ev = {'cxf:bean:': [{'file': 'r.xml', 'line': 5}], 'javax.xml.bind': [{'file': 'A.java', 'line': 3}], 'apiContextPath': [{'file': 'R.java', 'line': 9}]}
    vals = lambda a: {x['value'] for x in decide(a, {}, '4.14.0', evidence=ev)['dependencyEvidence']}
    assert vals('camel-cxf') == {'cxf:bean:'} and vals('camel-jaxb') == {'javax.xml.bind'} and vals('camel-swagger-java') == {'apiContextPath'}
    # test 4: dependency without specific evidence
    d = decide('camel-jaxb', {}, '4.14.0', evidence={'cxf:bean:': [{'file': 'r.xml', 'line': 5}]}, tests=3)
    assert d['dependencyEvidence'] == [] and d['usageConfidence'] == 'LOW' and d['migrationConfidence'] in ('MEDIUM', 'LOW')
    assert d['artifactConfidence'] == 'HIGH' and d['sourceMigration'] == 'CONDITIONAL' and d['prerequisites']
    # test 5: Blueprint reports runtimes, not artifacts
    d = decide('camel-blueprint', {}, '4.14.0')
    assert d['action'] == 'BLOCK' and d['code'] == 'ARCHITECTURAL_MIGRATION_REQUIRED' and d['alternatives'] == []
    assert d['runtimeOptions'] == ['Spring Boot 3', 'Quarkus 3', 'Camel Main'] and d['runtimeMigration'] == 'REQUIRED' and d['autoMigration'] == 'NO'
    md = report_markdown([d])
    assert 'Artifact target:\nN/A' in md and 'no verificado' not in md and 'Code usage found' not in md
    # test 6: ambiguous CXF never becomes camel-cxf-soap
    d = decide('camel-cxf', {}, '4.14.0')
    assert d['action'] == 'BLOCK' and d['code'] == 'CXF_USAGE_AMBIGUOUS' and d.get('target') is None
    d = decide('camel-cxf', {'cxf:': ['ns-prefix-only.xml']}, '4.14.0')
    assert d['code'] == 'CXF_USAGE_AMBIGUOUS'
    # SUPPORTED semantics (doc 38) and HIGH only with every factor
    ev = {'uri:direct': [{'file': 'R.java', 'line': 4}]}
    d = decide('camel-core', {}, '4.14.0', evidence=ev, tests=2)
    assert d['sourceCompatibility'] == 'REQUIRES_VALIDATION' and d['migrationConfidence'] == 'HIGH'
    assert decide('camel-core', {}, '4.14.0', evidence=ev)['migrationConfidence'] == 'MEDIUM'      # no tests: not validatable
    assert decide('camel-servlet', {}, '4.14.0')['runtimeConfigMigration'] == 'CONDITIONAL'
    print('ok')

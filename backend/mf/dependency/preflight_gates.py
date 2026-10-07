"""Candidate preflight gates (camel-correcciones-compatibilidad 22, 25, 26, 29): run in order before `mvn compile`.

    POM_SANITY_GATE -> CAMEL_COMPONENT_COMPATIBILITY_GATE -> CAMEL_VERSION_CONSISTENCY_GATE
    -> ARTIFACT_AVAILABILITY_GATE -> DEPENDENCY_VALIDATION_GATE -> CANDIDATE_COMPILE

The first critical failure is the root cause; the following gates are SKIPPED (not FAILED) and compile is skipped
(BLOCKED_BY_PREFLIGHT_GATE). Facts come from the candidate POMs and verified BOM snapshots, never from guesses."""
from __future__ import annotations

from pathlib import Path

ORDER = ('POM_SANITY_GATE', 'CAMEL_COMPONENT_COMPATIBILITY_GATE', 'CAMEL_VERSION_CONSISTENCY_GATE', 'ARTIFACT_AVAILABILITY_GATE',
         'DEPENDENCY_VALIDATION_GATE')
AVAILABILITY = {'EXISTS': 'PASS', 'MISSING': 'NOT_FOUND', 'REPO_UNAVAILABLE': 'REPOSITORY_BLOCKED', 'POLICY': 'REPOSITORY_BLOCKED',
                'CREDENTIALS': 'AUTH_REQUIRED', 'NOT_VERIFIED': 'NOT_VERIFIED'}
CAMEL_VERSIONED = ('org.apache.camel', 'org.apache.camel.springboot')  # camel-quarkus extensions follow Quarkus versions


def _major(v: str | None) -> str | None:
    return v.split('.')[0] if v and v[:1].isdigit() else None


def component_gate(modules, usage, target_camel) -> dict:
    from ..camel_compat import decide
    bad = []
    for m in modules:
        for d in m['dependencies']:
            if d['groupId'] != 'org.apache.camel':
                continue
            x = decide(d['artifactId'], usage, target_camel)
            if x['action'] == 'KEEP':
                continue
            code = x.get('code') or {'RENAME': 'INVALID_CAMEL_COMPONENT_MAPPING', 'REMOVE': 'REMOVED_CAMEL_COMPONENT'}[x['action']]
            action = {'RENAME': f"REPLACE_WITH {x.get('target')}", 'REMOVE': 'REMOVE_DEPENDENCY'}.get(
                x['action'], f"{x['status']}: " + (', '.join(a['name'] for a in x.get('alternatives', [])) or 'revisión manual'))
            bad.append({'artifact': d['artifactId'], 'file': m['file'], 'status': x['status'], 'code': code, 'target': x.get('target'),
                        'recommendedAction': action})
    return {'name': 'CAMEL_COMPONENT_COMPATIBILITY_GATE', 'status': 'FAIL' if bad else 'PASS', 'code': bad[0]['code'] if bad else None,
            'findings': bad}


def version_gate(modules, target_camel) -> dict:
    """doc 22: every Camel dependency (and imported Camel BOM) must share the target major version."""
    target = _major(target_camel)
    conflicts, unresolved = [], []
    for m in modules:
        for b in m.get('boms', []):
            g, a, v = (b.split(':') + [None, None])[:3]
            if g in CAMEL_VERSIONED and _major(v) and _major(v) != target:
                conflicts.append({'artifact': a, 'version': v, 'file': m['file'], 'kind': 'bom'})
        for d in m['dependencies']:
            if d['groupId'] not in CAMEL_VERSIONED:
                continue
            v = d.get('versionResolved')
            if _major(v) and _major(v) != target:
                conflicts.append({'artifact': d['artifactId'], 'version': v, 'file': m['file']})
            elif v and '${' in v:
                unresolved.append({'artifact': d['artifactId'], 'version': v, 'file': m['file']})
    status = 'FAIL' if conflicts else ('WARN' if unresolved else 'PASS')
    return {'name': 'CAMEL_VERSION_CONSISTENCY_GATE', 'status': status, 'code': 'CAMEL_VERSION_CONFLICT' if conflicts else None,
            'targetMajor': int(target) if target else None, 'conflicts': conflicts, 'unresolved': unresolved}


def availability_gate(modules, remote_check) -> dict:
    from .artifact_validator import CAMEL_GROUPS, check
    checks = []
    for m in modules:
        for d in m['dependencies']:
            if d['groupId'] in CAMEL_GROUPS:
                c = check(d | {'file': m['file']}, m.get('boms', []), remote_check)
                st = AVAILABILITY.get(c['status'], c['status'])
                if c['status'] == 'MISSING' and 'ningún BOM importado' in c['basis']:
                    st = 'VERSION_CONFLICT'  # exists nowhere in the imported BOMs: no resolvable version
                checks.append(c | {'status': st})
    fail = [c for c in checks if c['status'] in ('NOT_FOUND', 'VERSION_CONFLICT')]
    warn = [c for c in checks if c['status'] in ('REPOSITORY_BLOCKED', 'AUTH_REQUIRED', 'NOT_VERIFIED')]
    return {'name': 'ARTIFACT_AVAILABILITY_GATE', 'status': 'FAIL' if fail else ('WARN' if warn else 'PASS'),
            'code': 'UNSUPPORTED_TARGET_ARTIFACT' if fail else None, 'checks': checks,
            'reasons': [f"{c['artifact']}:{c['version'] or '(gestionada)'} {c['status']} ({c['basis']})" for c in fail + warn]}


def run(root: Path, target_camel: str, *, usage: dict, decisions=(), modules=None, remote_check=None) -> dict:
    from ..analysis.scanner import scan
    from ..config import get_settings
    from ..pom import normalizer
    from .artifact_validator import remote
    s = get_settings()
    remote_check = remote_check or (lambda g, a, v: remote(g, a, v, s.maven_mirror_url, s.maven_network))
    modules = modules if modules is not None else scan(root)['modules']
    steps = [lambda: normalizer.sanity(root), lambda: component_gate(modules, usage, target_camel), lambda: version_gate(modules, target_camel),
             lambda: availability_gate(modules, remote_check),
             lambda: {'name': 'DEPENDENCY_VALIDATION_GATE', **({'status': 'FAIL', 'code': 'DEPENDENCY_VALIDATION_FAILED',
                                                                 'reasons': [f"mapeo sin confianza: {d['artifact']}" for d in decisions if not d.get('confidence')]}
                                                                if any(not d.get('confidence') for d in decisions) else {'status': 'PASS', 'code': None})}]
    gates, failed = [], None
    for name, fn in zip(ORDER, steps):
        if failed:
            gates.append({'name': name, 'status': 'SKIPPED', 'reason': f'BLOCKED_BY_PREFLIGHT_GATE: {failed["name"]}'})
            continue
        g = fn()
        gates.append(g)
        if g['status'] == 'FAIL':
            failed = g
    root_cause = None
    if failed:
        first = (failed.get('findings') or failed.get('conflicts') or failed.get('issues')
                 or [c for c in failed.get('checks', []) if c['status'] in ('NOT_FOUND', 'VERSION_CONFLICT')] or [{}])[0]
        root_cause = {'primaryReason': failed['code'], 'gate': failed['name'], 'artifact': first.get('artifact') or first.get('detail'),
                      'version': first.get('version'), 'target': first.get('target') or f'Camel {target_camel}',
                      'recommendedAction': first.get('recommendedAction') or (f'alinear a Camel {target_camel}' if failed['code'] == 'CAMEL_VERSION_CONFLICT'
                                                                             else 'corregir el POM del candidato')}
    status = 'FAIL' if failed else ('WARN' if any(g['status'] == 'WARN' for g in gates) else 'PASS')
    return {'status': status, 'primary': failed['code'] if failed else None, 'gates': gates, 'rootCause': root_cause}


if __name__ == '__main__':  # self-check: member-phygital (doc 31) is stopped before compile, at the right gate
    import tempfile
    pom = '''<project><groupId>g</groupId><artifactId>m</artifactId><version>1</version>
  <dependencies>
    <dependency><groupId>org.apache.camel</groupId><artifactId>camel-core</artifactId><version>4.14.0</version></dependency>
    <dependency><groupId>org.apache.camel</groupId><artifactId>camel-openapi-java</artifactId><version>2.23.2</version></dependency>
    <dependency><groupId>org.apache.camel</groupId><artifactId>camel-cxf-soap</artifactId><version>2.23.2</version></dependency>
  </dependencies></project>
'''
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / 'pom.xml').write_text(pom)
        r = run(Path(d), '4.14.0', usage={}, remote_check=lambda *a: ('EXISTS', 'x'))
        st = {g['name']: g['status'] for g in r['gates']}
        assert st == {'POM_SANITY_GATE': 'PASS', 'CAMEL_COMPONENT_COMPATIBILITY_GATE': 'PASS', 'CAMEL_VERSION_CONSISTENCY_GATE': 'FAIL',
                      'ARTIFACT_AVAILABILITY_GATE': 'SKIPPED', 'DEPENDENCY_VALIDATION_GATE': 'SKIPPED'}, st
        assert r['primary'] == 'CAMEL_VERSION_CONFLICT' and {c['artifact'] for c in r['gates'][2]['conflicts']} == {'camel-openapi-java', 'camel-cxf-soap'}
        assert r['rootCause']['gate'] == 'CAMEL_VERSION_CONSISTENCY_GATE' and r['gates'][2]['targetMajor'] == 4
        (Path(d) / 'pom.xml').write_text(pom.replace('camel-openapi-java</artifactId><version>2.23.2', 'camel-swagger-java</artifactId><version>4.14.0'))
        r = run(Path(d), '4.14.0', usage={}, remote_check=lambda *a: ('EXISTS', 'x'))
        assert r['primary'] == 'ARTIFACT_REPLACEMENT_REQUIRED' and r['rootCause']['artifact'] == 'camel-swagger-java'
    print('ok')

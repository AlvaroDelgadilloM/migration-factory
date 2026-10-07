"""Artifact availability validation and the DEPENDENCY_VALIDATION_GATE (doc 19 §4 and §8).

Every Camel dependency of the candidate is checked *before* Maven builds it, so an invalid artifact (removed component
with a Camel 4 version, renamed artifact never renamed, version not managed by any imported BOM) stops the run with an
explicit reason instead of a compile failure minutes later. Verified BOM snapshots answer offline; only what they do not
cover is asked to the approved repository. Results distinguish a missing artifact from an unavailable repository,
credentials or network policy."""
from __future__ import annotations

import urllib.error
import urllib.request

from ..camel_compat import bom

CAMEL_GROUPS = ('org.apache.camel', 'org.apache.camel.springboot', 'org.apache.camel.quarkus')
BOM_OF = {'org.apache.camel': 'camel-bom', 'org.apache.camel.springboot': 'camel-spring-boot-bom'}
_cache: dict = {}


def remote(group: str, artifact: str, version: str, mirror: str, network: str = 'allowlist', timeout=10) -> tuple[str, str]:
    """(EXISTS | MISSING | REPO_UNAVAILABLE | CREDENTIALS | POLICY, detail) for g:a:v in the approved repository."""
    if network == 'offline':
        return 'POLICY', 'red de Maven en modo offline: no se consulta el repositorio'
    key = (group, artifact, version, mirror)
    if key not in _cache:
        url = f"{mirror.rstrip('/')}/{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.pom"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=timeout) as r:
                _cache[key] = ('EXISTS', url) if r.status == 200 else ('REPO_UNAVAILABLE', f'HTTP {r.status}')
        except urllib.error.HTTPError as e:
            _cache[key] = ('MISSING', url) if e.code == 404 else ('CREDENTIALS', f'HTTP {e.code}') if e.code in (401, 403) \
                else ('REPO_UNAVAILABLE', f'HTTP {e.code}')
        except (urllib.error.URLError, OSError) as e:
            _cache[key] = ('REPO_UNAVAILABLE', type(e).__name__)
    return _cache[key]


def check(dep: dict, boms: list[str], remote_check) -> dict:
    g, a = dep['groupId'], dep['artifactId']
    v, observed = dep.get('versionResolved'), dep.get('versionObserved')
    res = {'artifact': f'{g}:{a}', 'version': v, 'file': dep.get('file'), 'line': dep.get('line')}
    if v and '${' not in v and dep.get('versionSource') != 'unresolved':
        snap = bom(f'{g}:{BOM_OF[g]}:{v}') if g in BOM_OF else None
        if snap is not None:
            ok = f'{g}:{a}' in snap
            return res | {'status': 'EXISTS' if ok else 'MISSING', 'basis': f'{BOM_OF[g]} {v} (snapshot verificado)'}
        st, detail = remote_check(g, a, v)
        return res | {'status': st, 'basis': detail}
    if not observed or dep.get('versionSource') == 'unresolved':  # version from an imported BOM
        snaps = [(c, bom(c)) for c in boms]
        if any(s is not None and f'{g}:{a}' in s for _, s in snaps):
            return res | {'status': 'EXISTS', 'basis': 'gestionada por ' + ', '.join(c for c, s in snaps if s and f'{g}:{a}' in s)}
        if snaps and all(s is not None for _, s in snaps):
            return res | {'status': 'MISSING', 'basis': 'ningún BOM importado la gestiona: ' + ', '.join(boms)}
        return res | {'status': 'NOT_VERIFIED', 'basis': 'BOM sin snapshot verificado: ' + (', '.join(c for c, s in snaps if s is None) or 'ninguno importado')}
    return res | {'status': 'NOT_VERIFIED', 'basis': f'versión no resuelta estáticamente ({observed})'}


def gate(modules: list[dict], decisions=(), remote_check=None) -> dict:
    """modules: static scan of the candidate. decisions: compatibility decisions of the plan (each needs a confidence)."""
    from ..config import get_settings
    s = get_settings()
    remote_check = remote_check or (lambda g, a, v: remote(g, a, v, s.maven_mirror_url, s.maven_network))
    checks = []
    for m in modules:
        for d in m['dependencies']:
            if d['groupId'] in CAMEL_GROUPS:
                checks.append(check(d | {'file': m['file']}, m.get('boms', []), remote_check))
    bad = [c for c in checks if c['status'] in ('MISSING',)]
    infra = [c for c in checks if c['status'] in ('REPO_UNAVAILABLE', 'CREDENTIALS', 'POLICY', 'NOT_VERIFIED')]
    no_conf = [d['artifact'] for d in decisions if not d.get('confidence')]
    reasons = [f"{c['artifact']}:{c['version'] or '(gestionada)'} no existe ({c['basis']})" for c in bad] + \
              [f'mapeo sin confianza: {a}' for a in no_conf]
    status = 'FAIL' if reasons else ('WARN' if infra else 'PASS')
    return {'name': 'DEPENDENCY_VALIDATION_GATE', 'status': status, 'code': 'DEPENDENCY_VALIDATION_FAILED' if status == 'FAIL' else None,
            'reasons': reasons, 'unverified': [f"{c['artifact']}: {c['status']} ({c['basis']})" for c in infra], 'checks': checks}


if __name__ == '__main__':  # self-check: acceptance case 3 of part A (no Maven needed)
    deps = [{'groupId': 'org.apache.camel', 'artifactId': 'camel-xmljson', 'versionResolved': '4.14.0', 'versionObserved': '${camel.version}', 'versionSource': 'property'},
            {'groupId': 'org.apache.camel', 'artifactId': 'camel-http', 'versionResolved': '4.14.0', 'versionObserved': '${camel.version}', 'versionSource': 'property'},
            {'groupId': 'org.apache.camel.springboot', 'artifactId': 'camel-jms-starter', 'versionResolved': None, 'versionObserved': None, 'versionSource': 'unresolved'},
            {'groupId': 'org.apache.camel.springboot', 'artifactId': 'camel-swagger-java-starter', 'versionResolved': None, 'versionObserved': None, 'versionSource': 'unresolved'}]
    mods = [{'file': 'pom.xml', 'boms': ['org.apache.camel.springboot:camel-spring-boot-bom:4.14.9'], 'dependencies': deps}]
    g = gate(mods, [{'artifact': 'camel-http4', 'confidence': 'HIGH'}], remote_check=lambda *a: ('REPO_UNAVAILABLE', 'sin red'))
    st = {c['artifact']: c['status'] for c in g['checks']}
    assert st == {'org.apache.camel:camel-xmljson': 'MISSING', 'org.apache.camel:camel-http': 'EXISTS',
                  'org.apache.camel.springboot:camel-jms-starter': 'EXISTS', 'org.apache.camel.springboot:camel-swagger-java-starter': 'MISSING'}, st
    assert g['status'] == 'FAIL' and g['code'] == 'DEPENDENCY_VALIDATION_FAILED' and 'camel-xmljson:4.14.0' in g['reasons'][0]
    assert gate([], [{'artifact': 'x'}], remote_check=lambda *a: None)['reasons'] == ['mapeo sin confianza: x']
    print('ok')

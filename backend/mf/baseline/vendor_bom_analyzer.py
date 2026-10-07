"""PlatformDetector + VendorBomAnalyzer (camel-cxf-evidence-corrections 42): Red Hat Fuse / Karaf and vendor BOMs.

A vendor platform BOM (e.g. org.jboss.redhat-fuse:fuse-karaf-bom) manages much more than Camel (CXF, Karaf, OSGi, SLF4J,
Jackson...). When it cannot be resolved the baseline is BASELINE_BLOCKED_PLATFORM_BOM, never just "POM still invalid":
- the vendor BOM is never replaced by camel-bom; camel-bom may only recover the Camel dependencies (repair PARTIAL);
- versions it managed are never guessed (dependency-management-snapshot.json records what is left without a version);
- static analysis continues, recipes that need a valid Maven model are blocked, compile is NOT_EXECUTABLE.
Stdlib only; no network."""
from __future__ import annotations

import re
from urllib.parse import urlparse

VENDOR_GROUPS = {'org.jboss.redhat-fuse': 'RED_HAT', 'org.jboss.fuse': 'RED_HAT', 'org.jboss.fuse.bom': 'RED_HAT'}
EXPECTED_REPOSITORY = {'RED_HAT': 'https://maven.repository.redhat.com/ga/'}  # Red Hat's public GA repository (not Central)
CENTRAL_HOSTS = {'repo1.maven.org', 'repo.maven.apache.org', 'repo.maven.org', 'central.sonatype.com'}
VENDOR_HOSTS = {'maven.repository.redhat.com': 'RED_HAT', 'repository.jboss.org': 'RED_HAT'}
REASON_CODES = ('RED_HAT_FUSE_PLATFORM_DETECTED', 'VENDOR_BOM_UNRESOLVED', 'PRIVATE_VENDOR_REPOSITORY_REQUIRED', 'DEPENDENCY_MANAGEMENT_INCOMPLETE',
                'BASELINE_REPAIR_PARTIAL', 'VENDOR_BOM_RECOVERY_REQUIRED')
BLUEPRINT_TOKENS = ('OSGI-INF/blueprint', 'osgi.org/xmlns/blueprint')


def _bom_platform(artifact: str) -> str:
    a = artifact.lower()
    return 'FUSE_KARAF' if 'karaf' in a else 'FUSE_SPRING_BOOT' if 'springboot' in a or 'spring-boot' in a else 'FUSE'


def detect_platform(modules: list[dict], usage: dict | None = None) -> dict | None:
    """PlatformDetector: parent, imported BOMs, dependencies, packaging and Blueprint descriptors. None = no legacy platform signal."""
    usage = usage or {}
    signals, boms = [], []
    for m in modules:
        for b in m.get('boms', []):
            g, a, v = (b.split(':') + ['', ''])[:3]
            if g in VENDOR_GROUPS:
                signals.append(f'bom {b}')
                boms.append({'groupId': g, 'artifactId': a, 'version': v, 'type': 'VENDOR_PLATFORM_BOM', 'vendor': VENDOR_GROUPS[g],
                             'platform': _bom_platform(a), 'status': 'NOT_VERIFIED', 'file': m['file']})
        par = m.get('parent') or {}
        if par.get('groupId') in VENDOR_GROUPS:
            signals.append(f"parent {par['groupId']}:{par['artifactId']}:{par.get('version')}")
            boms.append({'groupId': par['groupId'], 'artifactId': par['artifactId'], 'version': par.get('version'), 'type': 'VENDOR_PLATFORM_PARENT',
                         'vendor': VENDOR_GROUPS[par['groupId']], 'platform': _bom_platform(par['artifactId']), 'status': 'NOT_VERIFIED', 'file': m['file']})
        for d in m.get('dependencies', []):
            if d['groupId'] in VENDOR_GROUPS or d['groupId'].startswith('org.apache.karaf'):
                signals.append(f"dependency {d['groupId']}:{d['artifactId']}")
        if m.get('packaging') == 'bundle':
            signals.append(f"packaging=bundle ({m['file']})")
    if any(usage.get(t) for t in BLUEPRINT_TOKENS):
        signals.append('OSGI-INF/blueprint')
    signals = list(dict.fromkeys(signals))
    fuse = any(b['vendor'] == 'RED_HAT' for b in boms) or any(s.startswith('dependency org.jboss') for s in signals)
    karaf = any('karaf' in s.lower() or 'bundle' in s or 'blueprint' in s for s in signals)
    if fuse:
        platform = 'RED_HAT_FUSE_SPRING_BOOT' if boms and all(b['platform'] == 'FUSE_SPRING_BOOT' for b in boms) else (
            'RED_HAT_FUSE_KARAF' if karaf else 'RED_HAT_FUSE')
    elif karaf:
        platform = 'APACHE_KARAF_OSGI'
    else:
        return None
    return {'platform': platform, 'signals': signals[:20], 'vendorBoms': boms}


def mark_unresolved(info: dict, model_errors: list[dict]) -> dict:
    """Vendor BOM/parent status from the Maven model check: UNRESOLVED if Maven names it as not resolvable."""
    text = '\n'.join(e.get('message', '') for e in model_errors)
    for b in info['vendorBoms']:
        coord = re.escape(f"{b['groupId']}:{b['artifactId']}")
        hit = re.search(rf'(Non-resolvable (?:import|parent) POM|Could not (?:find|transfer) artifact|not found)[^\n]*{coord}', text) \
            or re.search(rf'{coord}[^\n]*(was not found|could not be resolved|Could not find)', text)
        b['status'] = 'UNRESOLVED' if hit else ('RESOLVED' if not model_errors else b['status'])
    return info


def repository_kind(url: str, mirror: str | None = None) -> str:
    host = (urlparse(url).hostname or '').lower()
    if host in CENTRAL_HOSTS:
        return 'MAVEN_CENTRAL'
    if host in VENDOR_HOSTS:
        return 'VENDOR_REPOSITORY'
    if mirror and url.rstrip('/') == mirror.rstrip('/'):
        return 'CORPORATE_REPOSITORY'
    if host in ('localhost',) or re.match(r'^(10|127|192\.168|172\.(1[6-9]|2\d|3[01]))\.', host) or host.endswith(('.local', '.internal', '.corp', '.lan')):
        return 'PRIVATE_REPOSITORY'
    return 'UNKNOWN'


def repository_status(info: dict, configured: list[str], mirror: str | None = None) -> dict:
    """Is the vendor's repository among the approved ones? If not, the BOM needs a private/vendor repository configured."""
    kinds = {u: repository_kind(u, mirror) for u in configured}
    vendors = {b['vendor'] for b in info['vendorBoms'] if b['status'] == 'UNRESOLVED'}
    expected = {v: EXPECTED_REPOSITORY.get(v) for v in vendors}
    vendor_configured = any(k == 'VENDOR_REPOSITORY' for k in kinds.values())
    return {'configured': [{'url': u, 'kind': k} for u, k in kinds.items()], 'expected': expected,
            'vendorRepositoryConfigured': vendor_configured}


def snapshot(modules: list[dict], info: dict | None) -> list[dict]:
    """dependency-management-snapshot.json: where each dependency's version comes from; versionless ones point to the BOM that
    most likely managed them (one unresolved import BOM in the module -> HIGH, several -> MEDIUM). Never a guessed version."""
    unresolved = {}
    for b in (info or {}).get('vendorBoms', []):
        if b['status'] == 'UNRESOLVED':
            unresolved.setdefault(b['file'], []).append(f"{b['groupId']}:{b['artifactId']}")
    out = []
    for m in modules:
        boms = unresolved.get(m['file'], []) or [f'{b.split(":")[0]}:{b.split(":")[1]}' for b in m.get('boms', [])
                                                  if f'BOM {b} externo no resuelto localmente' in m.get('unresolved', [])]
        for d in m['dependencies']:
            art = f"{d['groupId']}:{d['artifactId']}"
            if d['versionResolved']:
                out.append({'artifact': art, 'resolvedVersion': d['versionResolved'], 'versionSource': d['versionSource'], 'sourceBom': None,
                            'confidence': 'HIGH', 'file': m['file']})
            else:
                src = boms[0] if len(boms) == 1 else None
                out.append({'artifact': art, 'resolvedVersion': None, 'versionSource': src.split(':')[1] if src else 'unresolved',
                            'sourceBom': src, 'candidateBoms': boms if len(boms) > 1 else None,
                            'confidence': 'HIGH' if src else ('MEDIUM' if boms else 'LOW'), 'file': m['file']})
    return out


def repair_outcome(repairs: list[dict], snap: list[dict]) -> dict:
    """§5-6: camel-bom may recover Camel only; whatever the vendor BOM managed and is still versionless keeps it PARTIAL."""
    covered = {a for r in repairs for a in r.get('covers', [])}
    still = sorted({e['artifact'] for e in snap if e['resolvedVersion'] is None and e['artifact'] not in covered})
    status = 'NONE' if not covered and still else 'PARTIAL' if still else 'SUCCESS' if covered else 'NOT_NEEDED'
    return {'status': status, 'recovered': len(covered), 'recoveredArtifacts': sorted(covered), 'stillUnresolved': still}


def configured_repositories() -> tuple[str, list[str]]:
    """(mirror, approved repositories) exactly as Maven runs get them (mavenops): extra ones only in allowlist mode."""
    from ..config import get_settings  # lazy: the scanner imports this module without the platform settings
    s = get_settings()
    extra = [r.split('|', 1)[1] for r in s.maven_extra_repos.split(',') if '|' in r] if s.maven_network == 'allowlist' else []
    return s.maven_mirror_url, [s.maven_mirror_url] + extra


def assess(modules, usage, model_errors, repairs, configured_repos, mirror=None) -> dict | None:
    """Whole analysis for the baseline: None when no legacy platform; 'blocked' when a vendor BOM/parent is unresolved."""
    return evaluate(detect_platform(modules, usage), modules, model_errors, repairs, configured_repos, mirror)


def evaluate(info, modules, model_errors, repairs, configured_repos, mirror=None) -> dict | None:
    """Second half of assess(), also used by the worker with the platform detected by the scan (scan.summary['platform'])."""
    if not info:
        return None
    info = {**info, 'vendorBoms': [dict(b) for b in info['vendorBoms']]}
    mark_unresolved(info, model_errors)
    snap = snapshot(modules, info)
    unresolved = [b for b in info['vendorBoms'] if b['status'] == 'UNRESOLVED']
    info['snapshot'] = snap
    info['repair'] = repair_outcome(repairs, snap)
    info['repositories'] = repository_status(info, configured_repos, mirror)
    info['blocked'] = bool(unresolved)
    codes = ['RED_HAT_FUSE_PLATFORM_DETECTED'] if info['platform'].startswith('RED_HAT_FUSE') else []
    if unresolved:
        primary = 'VENDOR_BOM_UNRESOLVED' if info['repositories']['vendorRepositoryConfigured'] else 'PRIVATE_VENDOR_REPOSITORY_REQUIRED'
        codes = [primary] + codes + ['VENDOR_BOM_UNRESOLVED', 'VENDOR_BOM_RECOVERY_REQUIRED']
        if info['repair']['stillUnresolved']:
            codes.append('DEPENDENCY_MANAGEMENT_INCOMPLETE')
        if info['repair']['status'] == 'PARTIAL':
            codes.append('BASELINE_REPAIR_PARTIAL')
        info['primaryReason'] = primary
        info['continuation'] = {'staticAnalysis': 'CONTINUE', 'xmlRouteAnalysis': 'CONTINUE', 'integrationInventory': 'CONTINUE',
                                'dependencyInventory': 'CONTINUE', 'semanticMavenAnalysis': 'PARTIAL', 'recipesRequiringValidModel': 'BLOCKED',
                                'compile': 'NOT_EXECUTABLE', 'test': 'NOT_RUN'}
        info['requiredAction'] = ('Configurar el repositorio del proveedor (' + ', '.join(filter(None, info['repositories']['expected'].values()))
                                  + ') como repositorio aprobado (MF_MAVEN_EXTRA_REPOS) o proveer un dependencyManagement equivalente; '
                                  'no se reemplaza el BOM del proveedor por camel-bom ni se inventan versiones.')
    info['reasonCodes'] = list(dict.fromkeys(codes))
    info['runtimeMigration'] = 'REQUIRED' if info['platform'] in ('RED_HAT_FUSE_KARAF', 'APACHE_KARAF_OSGI') else 'REVIEW'
    return info


def report_section(info: dict | None, camel: list[str] | None = None) -> str:
    """§21 block for baseline-report.md."""
    if not info:
        return ''
    bom = next((b for b in info['vendorBoms'] if b['status'] == 'UNRESOLVED'), (info['vendorBoms'] or [None])[0])
    rep = info.get('repair') or {}
    cont = info.get('continuation') or {}
    lines = ['## Plataforma de origen', '', '```text', 'Source platform:', info['platform']]
    if camel:
        lines += ['', 'Camel:', ', '.join(camel)]
    if bom:
        lines += ['', 'Vendor BOM:', f"{bom['groupId']}:{bom['artifactId']}:", bom['version'] or '?', '', 'Vendor BOM status:', bom['status']]
    if info.get('blocked'):
        lines += ['', 'Baseline repair:', rep.get('status', 'NONE')]
        if rep.get('recovered'):
            lines += ['', 'Recovered:', f"{rep['recovered']} Camel dependencies (camel-bom)"]
        if rep.get('stillUnresolved'):
            lines += ['', 'Still unresolved:'] + [f'- {a}' for a in rep['stillUnresolved'][:30]]
        lines += ['', 'Static analysis:', 'PASS', '', 'Semantic Maven analysis:', cont['semanticMavenAnalysis'], '', 'Recipes:',
                  cont['recipesRequiringValidModel'], '', 'Compile:', cont['compile'], '', 'Test:', cont['test'], '', 'Migration status:', 'BLOCKED',
                  '', 'Primary reason:', info['primaryReason']]
    lines += ['', 'Runtime migration:', info['runtimeMigration'], '', 'Signals:'] + [f'- {s}' for s in info['signals']] + ['```', '']
    if info.get('requiredAction'):
        lines += ['Acción requerida: ' + info['requiredAction'], '']
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':  # self-check: doc 42 acceptance cases 1, 2, 3, 7
    fuse = 'org.jboss.redhat-fuse:fuse-karaf-bom:7.10.0.fuse-sb2-7_10_1-00008-redhat-00001'
    mods = [{'file': 'pom.xml', 'packaging': 'bundle', 'boms': [fuse], 'parent': None, 'unresolved': [f'BOM {fuse} externo no resuelto localmente'],
             'dependencies': [{'groupId': 'org.apache.camel', 'artifactId': 'camel-core', 'versionResolved': None, 'versionSource': 'unresolved'},
                              {'groupId': 'org.apache.camel', 'artifactId': 'camel-blueprint', 'versionResolved': None, 'versionSource': 'unresolved'},
                              {'groupId': 'org.slf4j', 'artifactId': 'slf4j-api', 'versionResolved': None, 'versionSource': 'unresolved'},
                              {'groupId': 'junit', 'artifactId': 'junit', 'versionResolved': '4.13.2', 'versionSource': 'declared'}]}]
    errs = [{'message': f"Non-resolvable import POM: Could not find artifact {fuse.replace('bom:', 'bom:pom:')} in mf-approved (https://repo1.maven.org/maven2)"}]
    repairs = [{'covers': ['org.apache.camel:camel-core', 'org.apache.camel:camel-blueprint']}]
    a = assess(mods, {'OSGI-INF/blueprint': ['x']}, errs, repairs, ['https://repo1.maven.org/maven2'])
    assert a['platform'] == 'RED_HAT_FUSE_KARAF' and a['blocked'] and a['vendorBoms'][0]['status'] == 'UNRESOLVED'
    assert a['primaryReason'] == 'PRIVATE_VENDOR_REPOSITORY_REQUIRED' and 'BASELINE_REPAIR_PARTIAL' in a['reasonCodes']
    assert a['repair'] == {'status': 'PARTIAL', 'recovered': 2, 'recoveredArtifacts': ['org.apache.camel:camel-blueprint', 'org.apache.camel:camel-core'],
                           'stillUnresolved': ['org.slf4j:slf4j-api']}
    slf = next(e for e in a['snapshot'] if e['artifact'] == 'org.slf4j:slf4j-api')
    assert slf['resolvedVersion'] is None and slf['sourceBom'] == 'org.jboss.redhat-fuse:fuse-karaf-bom' and slf['confidence'] == 'HIGH'  # no guess
    assert 'Source platform:\nRED_HAT_FUSE_KARAF' in report_section(a, ['2.23.2'])
    b = assess(mods, {}, errs, repairs, ['https://repo1.maven.org/maven2', 'https://maven.repository.redhat.com/ga/'])
    assert b['primaryReason'] == 'VENDOR_BOM_UNRESOLVED'                                   # vendor repo configured but BOM still missing
    assert assess([dict(mods[0], boms=[], packaging='jar', unresolved=[])], {}, [], [], [])is None
    assert repository_kind('https://nexus.acme.internal/repo') == 'PRIVATE_REPOSITORY'
    print('ok')

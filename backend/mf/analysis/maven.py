"""Static Maven model: modules, parent chain, properties, dependencyManagement and local BOMs.

Only files inside the repository are read. Anything external (remote parent, remote BOM,
settings.xml, activated profiles) is reported as unresolved; the worker may later replace
these values with `mvn help:effective-pom` output run inside the sandbox.
"""
import re
from pathlib import Path

from . import xmlparse

PROP = re.compile(r'\$\{([^}]+)\}')
JAVA_PROPS = ('maven.compiler.release', 'maven.compiler.target', 'maven.compiler.source', 'java.version')


def _deps(node, line_offset=0):
    out = []
    for d in node.find('dependency') if node is not None else []:
        out.append({k: d.findtext(k, '') for k in ('groupId', 'artifactId', 'version', 'scope', 'type')} | {'line': d.line})
    return out


def read_pom(path: Path, rel: str):
    root = xmlparse.parse(path.read_text(encoding='utf-8'))
    parent = root.first('parent')
    pom = {
        'file': rel,
        'groupId': root.findtext('groupId') or (parent.findtext('groupId') if parent else None),
        'artifactId': root.findtext('artifactId', 'unknown'),
        'version': root.findtext('version') or (parent.findtext('version') if parent else None),
        'packaging': root.findtext('packaging', 'jar'),
        'packagingLine': (root.first('packaging').line if root.first('packaging') else None),
        'parent': None,
        'properties': {c.tag: c.text.strip() for c in (root.first('properties').children if root.first('properties') else [])},
        'modules': [m.text.strip() for m in root.find('modules/module')],
        'dependencies': _deps(root.first('dependencies')),
        'managed': _deps(root.first('dependencyManagement/dependencies')),
        'profiles': [{'id': p.findtext('id', ''), 'activeByDefault': p.findtext('activation/activeByDefault') == 'true'}
                     for p in root.find('profiles/profile')],
        'compilerConfig': {},
    }
    for plugin in root.find('build/plugins/plugin') + root.find('build/pluginManagement/plugins/plugin'):
        if plugin.findtext('artifactId') == 'maven-compiler-plugin':
            for k in ('release', 'source', 'target'):
                v = plugin.findtext(f'configuration/{k}')
                if v:
                    pom['compilerConfig'][k] = v
    if parent is not None:
        pom['parent'] = {'groupId': parent.findtext('groupId'), 'artifactId': parent.findtext('artifactId'),
                         'version': parent.findtext('version'),
                         'relativePath': parent.findtext('relativePath', '../pom.xml')}
    return pom


def _within(root: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def resolve(root: Path, poms: dict):
    """poms: rel path -> raw pom. Returns list of resolved module dicts."""
    by_coords = {(p['groupId'], p['artifactId'], p['version']): rel for rel, p in poms.items()}
    modules = []
    for rel, pom in poms.items():
        unresolved = []
        chain = [pom]
        cur, seen = pom, {rel}
        while cur['parent']:
            par = cur['parent']
            cur_dir = (root / cur['file']).parent
            cand = (cur_dir / (par['relativePath'] or '')).resolve() if par['relativePath'] else None
            if cand is not None and cand.is_dir():
                cand = cand / 'pom.xml'
            parent_rel = None
            if cand is not None and _within(root, cand) and cand.is_file():
                r = str(cand.relative_to(root))
                if r in poms and poms[r]['artifactId'] == par['artifactId']:
                    parent_rel = r
            parent_rel = parent_rel or by_coords.get((par['groupId'], par['artifactId'], par['version']))
            if not parent_rel or parent_rel in seen:
                unresolved.append(f"parent {par['groupId']}:{par['artifactId']}:{par['version']} no disponible en el repositorio")
                break
            seen.add(parent_rel)
            cur = poms[parent_rel]
            chain.append(cur)

        props = {}
        for p in reversed(chain):
            props.update(p['properties'])
        props.update({'project.groupId': pom['groupId'] or '', 'project.artifactId': pom['artifactId'],
                      'project.version': pom['version'] or '', 'pom.version': pom['version'] or ''})
        if pom['parent']:
            props['project.parent.version'] = pom['parent']['version'] or ''

        def interp(v):
            if not v:
                return v, True
            for _ in range(10):
                new = PROP.sub(lambda m: props.get(m[1], m[0]), v)
                if new == v:
                    break
                v = new
            return v, not PROP.search(v)

        managed = {}
        boms = []
        for p in reversed(chain):
            for m in p['managed']:
                g, a = interp(m['groupId'])[0], interp(m['artifactId'])[0]
                ver, ok = interp(m['version'])
                if m['scope'] == 'import' and m['type'] == 'pom':
                    boms.append((g, a, ver))
                    continue
                managed[(g, a)] = (ver, ok, 'managed-local')
        for g, a, v in boms:
            bom_rel = by_coords.get((g, a, v))
            if not bom_rel:
                unresolved.append(f'BOM {g}:{a}:{v} externo no resuelto localmente')
                continue
            for m in poms[bom_rel]['managed']:
                managed.setdefault((m['groupId'], m['artifactId']), (m['version'], not PROP.search(m['version'] or ''), 'bom-local'))

        deps = []
        for d in pom['dependencies']:
            g, a = interp(d['groupId'])[0], interp(d['artifactId'])[0]
            if d['version']:
                ver, ok = interp(d['version'])
                src = 'declared' if d['version'] == ver else 'property'
            elif (g, a) in managed:
                ver, ok, src = managed[(g, a)]
            else:
                ver, ok, src = None, False, 'unresolved'
            deps.append({'groupId': g, 'artifactId': a, 'scope': d['scope'] or 'compile', 'line': d['line'],
                         'versionObserved': d['version'] or None,
                         'versionResolved': ver if ok else None,
                         'versionSource': src if ok else 'unresolved'})

        java_obs = java_res = java_src = None
        for k in ('release', 'target', 'source'):
            if pom['compilerConfig'].get(k):
                java_obs, java_src = pom['compilerConfig'][k], f'maven-compiler-plugin/{k}'
                break
        if not java_obs:
            for k in JAVA_PROPS:
                if k in props:
                    java_obs, java_src = props[k], f'property {k}'
                    break
        if java_obs:
            v, ok = interp(java_obs)
            java_res = v if ok else None

        camel = sorted({d['versionResolved'] or '?' for d in deps if d['groupId'].startswith('org.apache.camel')})
        modules.append({
            'file': rel,
            'groupId': interp(pom['groupId'])[0], 'artifactId': pom['artifactId'],
            'version': interp(pom['version'])[0], 'packaging': pom['packaging'], 'packagingLine': pom['packagingLine'],
            'parent': pom['parent'] and {k: pom['parent'][k] for k in ('groupId', 'artifactId', 'version')},
            'modules': pom['modules'],
            'javaObserved': java_obs, 'javaResolved': java_res, 'javaSource': java_src,
            'camelVersions': camel,
            'dependencies': deps,
            'profiles': pom['profiles'],
            'boms': [f'{g}:{a}:{v}' for g, a, v in boms],
            'resolution': 'static-partial' if unresolved or any(d['versionResolved'] is None for d in deps) else 'static-local',
            'unresolved': unresolved,
        })
    return modules

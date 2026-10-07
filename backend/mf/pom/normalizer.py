"""POM text edits that need no dependency resolution, so they can run where OpenRewrite cannot parse the POM yet.

- apply_mapping (doc 24 phase 2): Camel compatibility decisions (rename / remove) and alignment of every remaining
  org.apache.camel literal version to the target Camel version. Never a blind bump: only SUPPORTED artifacts are aligned.
- normalize (doc 23): one declaration per dependency key (groupId, artifactId, type, classifier) and per plugin
  (groupId, artifactId) in each section; ${artifactId}/${groupId}/${version} -> ${project.*} when not user properties.
- sanity (doc 23): POM_SANITY_GATE — duplicates, unreadable XML, unresolved placeholders.
Edits are located on the original text (formatting kept) and never touch exclusions or plugin configuration."""
from __future__ import annotations

import re
from pathlib import Path

DEP = re.compile(r'[ \t]*<dependency>(?:(?!</dependency>).)*</dependency>[ \t]*\n?', re.S)
PLUGIN = re.compile(r'[ \t]*<plugin>(?:(?!</plugin>).)*</plugin>[ \t]*\n?', re.S)
SECTION = {'dependencies': re.compile(r'<dependencies>(.*?)</dependencies>', re.S), 'plugins': re.compile(r'<plugins>(.*?)</plugins>', re.S)}
BUILTIN = re.compile(r'^(project\.|pom\.|env\.|settings\.|java\.|os\.|user\.|maven\.|basedir$|revision$|sha1$|changelist$)')


def poms(root: Path):
    return sorted(p for p in root.rglob('pom.xml') if not any(x in ('target', '.git', 'node_modules') for x in p.relative_to(root).parts))


def _tag(block: str, name: str) -> str | None:
    head = re.sub(r'<exclusions>.*?</exclusions>|<configuration>.*?</configuration>|<executions>.*?</executions>|<dependencies>.*?</dependencies>',
                  '', block, flags=re.S)
    m = re.search(rf'<{name}>\s*([^<]*?)\s*</{name}>', head)
    return m[1] if m else None


def dep_key(block: str):
    return (_tag(block, 'groupId'), _tag(block, 'artifactId'), _tag(block, 'type') or 'jar', _tag(block, 'classifier') or '')


def _dedupe(text: str, section: str, item: re.Pattern, key, kind: str, file: str, changes: list, conflicts: list) -> str:
    def fix_section(m):
        body, seen, out, pos = m[1], {}, [], 0
        for b in item.finditer(body):
            k = key(b[0])
            out.append(body[pos:b.start()])
            pos = b.end()
            if k in seen:
                first = seen[k]
                if kind == 'dependency' and (_tag(first, 'scope') or 'compile') != (_tag(b[0], 'scope') or 'compile'):
                    conflicts.append({'file': file, 'kind': 'SCOPE_CONFLICT', 'key': ':'.join(map(str, k[:2])),
                                      'detail': f"{_tag(first, 'scope') or 'compile'} vs {_tag(b[0], 'scope') or 'compile'}"})
                if _tag(first, 'version') and not _tag(b[0], 'version'):  # prefer the declaration managed by a BOM
                    idx = out.index(first) if first in out else None
                    if idx is not None:
                        out[idx] = b[0]
                        seen[k] = b[0]
                changes.append({'file': file, 'change': f"{kind} duplicado eliminado: {':'.join(str(x) for x in k[:2])}"})
                continue
            seen[k] = b[0]
            out.append(b[0])
        out.append(body[pos:])
        return m[0].replace(body, ''.join(out), 1)
    return SECTION[section].sub(fix_section, text)


def normalize(root: Path) -> dict:
    changes, conflicts = [], []
    for p in poms(root):
        rel = p.relative_to(root).as_posix()
        text = p.read_text(encoding='utf-8')
        new = _dedupe(text, 'dependencies', DEP, dep_key, 'dependency', rel, changes, conflicts)
        new = _dedupe(new, 'plugins', PLUGIN, lambda b: (_tag(b, 'groupId') or 'org.apache.maven.plugins', _tag(b, 'artifactId')),
                      'plugin', rel, changes, conflicts)
        props = set(re.findall(r'<properties>.*?</properties>', new, re.S) and re.findall(r'<([\w.\-]+)>', re.search(r'<properties>(.*?)</properties>', new, re.S)[1]))
        for legacy in ('artifactId', 'groupId', 'version'):  # deprecated implicit properties -> ${project.*} (SAFE_AUTOFIX)
            if legacy not in props and f'${{{legacy}}}' in new:
                new = new.replace(f'${{{legacy}}}', f'${{project.{legacy}}}')
                changes.append({'file': rel, 'change': f'${{{legacy}}} → ${{project.{legacy}}} (SAFE_AUTOFIX)'})
        if new != text:
            p.write_text(new, encoding='utf-8')
    return {'changes': changes, 'conflicts': conflicts}


def apply_mapping(root: Path, decisions: list[dict], target_version: str, supported: set[str]) -> list[dict]:
    """decisions: camel_compat.decide() results (RENAME/REMOVE applied); supported: artifactIds present in the target camel-bom."""
    rename = {d['artifact']: d['target'] for d in decisions if d['action'] == 'RENAME'}
    remove = {d['artifact'] for d in decisions if d['action'] == 'REMOVE'}
    major = target_version.split('.')[0]
    results = []
    for p in poms(root):
        rel = p.relative_to(root).as_posix()
        text = p.read_text(encoding='utf-8')

        def one(m):
            b = m[0]
            if _tag(b, 'groupId') != 'org.apache.camel':
                return b
            a, v = _tag(b, 'artifactId'), _tag(b, 'version')
            if a in remove:
                results.append({'file': rel, 'op': 'remove', 'artifact': a, 'applied': True})
                return ''
            if a in rename:
                b = re.sub(rf'<artifactId>\s*{re.escape(a)}\s*</artifactId>', f'<artifactId>{rename[a]}</artifactId>', b, count=1)
                results.append({'file': rel, 'op': 'rename', 'artifact': a, 'target': rename[a], 'applied': True})
                a = rename[a]
            if v and not v.startswith('${') and v.split('.')[0] != major and a in supported:  # literal legacy version left behind
                b = re.sub(rf'<version>\s*{re.escape(v)}\s*</version>', f'<version>{target_version}</version>', b, count=1)
                results.append({'file': rel, 'op': 'align-version', 'artifact': a, 'from': v, 'to': target_version, 'applied': True})
            return b
        new = DEP.sub(one, text)
        if new != text:
            p.write_text(new, encoding='utf-8')
    return results


def run_mapping_step(root: Path, mapping: dict) -> list[dict]:
    """Executes a planned `pomMapping` step (shared by the CLI and the worker)."""
    from ..camel_compat import bom
    target = mapping['targetVersion']
    supported = {a.split(':', 1)[1] for a in (bom(f'org.apache.camel:camel-bom:{target}') or ()) if a.startswith('org.apache.camel:')}
    return apply_mapping(root, mapping['decisions'], target, supported)


def sanity(root: Path) -> dict:
    """POM_SANITY_GATE: FAIL on unreadable XML or duplicates; WARN on placeholders that no POM of the project defines."""
    from ..analysis import xmlparse
    issues, warnings, defined = [], [], set()
    texts = {p.relative_to(root).as_posix(): p.read_text(encoding='utf-8', errors='replace') for p in poms(root)}
    for t in texts.values():
        for sec in re.findall(r'<properties>(.*?)</properties>', t, re.S):
            defined |= set(re.findall(r'<([\w.\-]+)>', sec))
    for rel, t in texts.items():
        try:
            xmlparse.parse(t)
        except Exception as e:  # noqa: BLE001
            issues.append({'file': rel, 'issue': 'INVALID_XML', 'detail': type(e).__name__})
            continue
        for section, item, kind in (('dependencies', DEP, 'dependency'), ('plugins', PLUGIN, 'plugin')):
            for sec in SECTION[section].findall(t):
                keys = [dep_key(b) if kind == 'dependency' else (_tag(b, 'groupId') or 'org.apache.maven.plugins', _tag(b, 'artifactId'))
                        for b in (m[0] for m in item.finditer(sec))]
                for k in {k for k in keys if keys.count(k) > 1}:
                    issues.append({'file': rel, 'issue': f'DUPLICATE_{kind.upper()}', 'detail': ':'.join(str(x) for x in k[:2])})
        for ph in sorted(set(re.findall(r'\$\{([^}]+)\}', t))):
            if ph not in defined and not BUILTIN.match(ph):
                warnings.append({'file': rel, 'issue': 'UNRESOLVED_PLACEHOLDER', 'detail': ph})
    return {'name': 'POM_SANITY_GATE', 'status': 'FAIL' if issues else ('WARN' if warnings else 'PASS'),
            'code': 'POM_SANITY_ERROR' if issues else None, 'issues': issues, 'warnings': warnings}


if __name__ == '__main__':  # self-check: the member-phygital shape (doc 31)
    import tempfile
    pom = '''<project><artifactId>m</artifactId><packaging>bundle</packaging>
  <properties><camel.version>4.14.0</camel.version></properties>
  <dependencies>
    <dependency><groupId>org.apache.camel</groupId><artifactId>camel-core</artifactId><version>${camel.version}</version></dependency>
    <dependency><groupId>org.apache.camel</groupId><artifactId>camel-swagger-java</artifactId><version>2.23.2</version></dependency>
    <dependency><groupId>org.apache.camel</groupId><artifactId>camel-cxf</artifactId><version>2.23.2</version>
      <exclusions><exclusion><groupId>x</groupId><artifactId>camel-cxf</artifactId></exclusion></exclusions></dependency>
    <dependency><groupId>org.apache.camel</groupId><artifactId>camel-openapi-java</artifactId></dependency>
    <dependency><groupId>org.apache.camel</groupId><artifactId>camel-cdi</artifactId><version>2.23.2</version></dependency>
  </dependencies>
  <build><finalName>${artifactId}</finalName><plugins>
    <plugin><artifactId>maven-compiler-plugin</artifactId></plugin><plugin><artifactId>maven-compiler-plugin</artifactId></plugin>
  </plugins></build></project>
'''
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / 'pom.xml').write_text(pom)
        dec = [{'artifact': 'camel-swagger-java', 'action': 'RENAME', 'target': 'camel-openapi-java'},
               {'artifact': 'camel-cxf', 'action': 'RENAME', 'target': 'camel-cxf-soap'}, {'artifact': 'camel-cdi', 'action': 'REMOVE'}]
        r = apply_mapping(root, dec, '4.14.0', {'camel-core', 'camel-openapi-java', 'camel-cxf-soap'})
        assert {x['op'] for x in r} == {'rename', 'align-version', 'remove'}
        t = (root / 'pom.xml').read_text()
        assert 'camel-cdi' not in t and '2.23.2' not in t and '<artifactId>camel-cxf</artifactId></exclusion>' in t  # exclusions untouched
        assert sanity(root)['status'] == 'FAIL'  # duplicate camel-openapi-java + duplicate compiler plugin
        n = normalize(root)
        t = (root / 'pom.xml').read_text()
        assert t.count('<artifactId>camel-openapi-java</artifactId>') == 1 and t.count('maven-compiler-plugin') == 1
        assert '<artifactId>camel-openapi-java</artifactId></dependency>' in t  # the BOM-managed declaration is kept
        assert '${project.artifactId}' in t and any('SAFE_AUTOFIX' in c['change'] for c in n['changes'])
        assert sanity(root)['status'] == 'PASS'
        (root / 'pom.xml').write_text(t.replace('${camel.version}', '${camel.version.typo}'))
        assert sanity(root)['warnings'][0]['detail'] == 'camel.version.typo'
    print('ok')

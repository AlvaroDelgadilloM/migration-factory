"""Configuration keys report for the tomcat-war profile: the keys the code reads vs the ones the repository defines.

Fuse/Karaf bundles read most of their configuration from etc/*.cfg on the server, which is not in the repository. A key
used and not defined here must come from the `mf.etc` folder of the target Tomcat; missing it is the typical startup error.
Sources of "used": Camel placeholders `{{key}}` (Java/XML), Blueprint `${key}` placeholders, `@PropertyInject("key")`.
Sources of "defined": *.properties / *.cfg in the repository and Blueprint defaults (`<cm:property>` / `<property>` inside a
property-placeholder). A checklist (REVIEW), never a failure: the value may live in an etc folder the tool does not see."""
from __future__ import annotations

import re
from pathlib import Path

SKIP = {'target', '.git', 'node_modules'}
CAMEL = re.compile(r'\{\{\s*(?:env:|sys:)?([\w.\-]+?)(?::[^}]*)?\s*\}\}')
BLUEPRINT = re.compile(r'\$\{([\w.\-]+)(?::[^}]*)?\}')
INJECT = re.compile(r'@PropertyInject\(\s*(?:value\s*=\s*)?"([\w.\-]+)"')
# Camel Simple language / Spring EL names that look like ${key} but are not configuration
SIMPLE = re.compile(r'^(body|in\.|out\.|header|headers|exchangeProperty|exchangeId|property\.|properties|date|bean|exception|'
                    r'camelId|camelContext|routeId|id$|messageTimestamp|random|ref|type|null|sys\.|env\.|file|threadName|'
                    r'variable|variables|jsonpath|xpath|collate|skip|empty|trim|project\.|basedir|camel\.version)')
DEFAULTS = re.compile(r'<(?:cm:)?property\s+name="([\w.\-]+)"\s+value=')
PROP_LINE = re.compile(r'^\s*([\w.\-]+)\s*[=:]', re.M)


def _files(root: Path, suffixes):
    for p in root.rglob('*'):
        if p.suffix in suffixes and p.is_file() and not any(x in SKIP for x in p.relative_to(root).parts):
            yield p


def scan(source: Path) -> dict:
    source = Path(source)
    used, defined = {}, set()
    for f in _files(source, ('.java', '.xml')):
        text = f.read_text(encoding='utf-8', errors='replace')
        rel = f.relative_to(source).as_posix()
        if f.name == 'pom.xml':
            continue
        found = [(m.start(), m[1]) for m in CAMEL.finditer(text)] + [(m.start(), m[1]) for m in INJECT.finditer(text)]
        if f.suffix == '.xml' and 'blueprint' in text[:2000]:
            found += [(m.start(), m[1]) for m in BLUEPRINT.finditer(text) if not SIMPLE.match(m[1])]
            if 'property-placeholder' in text:
                defined |= set(DEFAULTS.findall(text))
        for pos, key in found:
            used.setdefault(key, [])
            if len(used[key]) < 5:
                used[key].append(f'{rel}:{text.count(chr(10), 0, pos) + 1}')
    for f in _files(source, ('.properties', '.cfg')):
        defined |= set(PROP_LINE.findall(f.read_text(encoding='utf-8', errors='replace')))
    missing = sorted(k for k in used if k not in defined)
    return {'status': 'REVIEW' if missing else 'PASS', 'used': len(used), 'definedInRepository': len(defined & set(used)),
            'missing': [{'key': k, 'usedAt': used[k]} for k in missing],
            'note': 'Claves que el código usa y el repositorio no define: deben estar en la carpeta etc del Tomcat destino (-Dmf.etc).'}


def markdown(r: dict) -> str:
    out = ['## Claves de configuración', '', f"Usadas: {r['used']} · definidas en el repositorio: {r['definedInRepository']} · "
           f"sin definir aquí: {len(r['missing'])} ({r['status']})", '', r['note'], '']
    if r['missing']:
        out += ['| Clave | Dónde se usa |', '|---|---|'] + [f"| `{m['key']}` | {', '.join(m['usedAt'])} |" for m in r['missing'][:300]] + ['']
    return '\n'.join(out)


if __name__ == '__main__':  # self-check
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / 'm/src/main/resources/OSGI-INF/blueprint').mkdir(parents=True)
        (root / 'm/src/main/java/demo').mkdir(parents=True)
        (root / 'm/src/main/resources/OSGI-INF/blueprint/ctx.xml').write_text(
            '<blueprint xmlns="http://www.osgi.org/xmlns/blueprint/v1.0.0">\n<cm:property-placeholder persistent-id="x">\n'
            '<cm:default-properties><cm:property name="timeout" value="30"/></cm:default-properties></cm:property-placeholder>\n'
            '<bean id="b"><property name="url" value="${db.url}"/><property name="t" value="${timeout}"/></bean>\n'
            '<route><setBody><simple>${body} ${header.x}</simple></setBody></route>\n</blueprint>\n')
        (root / 'm/src/main/java/demo/R.java').write_text('class R { void c() { from("timer:{{poll.period}}").to("{{svc.url}}"); }\n'
                                                          '  @PropertyInject("retries") int r; }\n')
        (root / 'm/src/main/resources/app.properties').write_text('svc.url=http://x\n')
        r = scan(root)
        assert [m['key'] for m in r['missing']] == ['db.url', 'poll.period', 'retries'] and r['status'] == 'REVIEW', r
        assert r['missing'][0]['usedAt'] == ['m/src/main/resources/OSGI-INF/blueprint/ctx.xml:4'] and r['used'] == 5
    print('ok')

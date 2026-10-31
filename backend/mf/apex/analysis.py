"""What each APEX application export brings and what stands in the way of each version jump. Reads the exports as text, line by
line (they reach 50 MB, most of it binary files): nothing runs and no database is needed. Keeps counts and locations, never
user names, e-mail addresses or binary content; other .sql files of the ZIP only give the names of the objects they create."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[3] / 'rules' / 'apex' / 'catalog.json'
HEAD_BYTES = 8192
MAX_LOCATIONS = 20
MAX_MISSING = 20
SEVERITIES = ('BLOCK', 'REVIEW', 'INFO')

_SECTION = re.compile(r'^prompt --application/(pages/page_(\d+)|shared_components/plugins/\w+/([\w.\-]+))')
_STAT = re.compile(r'^--\s+([A-Za-z][A-Za-z ]*):\s+([\d,]+)\s*$')
_ATTR = re.compile(r"^\s*,?p_(\w+)=>'([^']*)'")
_NUM_ATTR = re.compile(r'^\s*,?p_(\w+)=>(\d+)')
_PLUGIN_USE = re.compile(r"'PLUGIN_([A-Z0-9_.\-]+)'")
_HOST = re.compile(r'https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})')
_DDL = re.compile(r'^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?(?:UNIQUE\s+)?(TABLE|VIEW|PACKAGE(?:\s+BODY)?|TRIGGER|SEQUENCE|'
                  r'FUNCTION|PROCEDURE|TYPE|SYNONYM|MATERIALIZED\s+VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?"?(?:\w+"?\."?)?(\w+)"?', re.I)


def catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def version_key(v: str) -> tuple:
    return tuple(int(x) for x in v.split('.')[:2])


def short_version(release: str | None) -> str | None:
    m = re.match(r'(\d+\.\d+)', release or '')
    return m.group(1) if m else None


def parse_head(name: str, raw: bytes) -> dict | None:
    """The header of an export from its first bytes, or None when it is not one."""
    if b'.import_begin' not in raw or not (b'Application Express' in raw or b'APEX' in raw):
        return None
    head = raw.decode('utf-8', 'replace')

    def find(rx):
        m = re.search(rx, head)
        return m.group(1).strip() if m else None
    return {'file': name, 'appId': find(r'p_default_application_id=>(\d+)'), 'name': find(r'--\s+Name:\s+(.+)'), 'owner': find(r"p_default_owner=>'([^']+)'"),
            'release': find(r"p_release=>'([^']+)'")}


def peek(path: Path) -> dict | None:
    with open(path, 'rb') as f:
        return parse_head(path.name, f.read(HEAD_BYTES))


def _rules(cat: dict) -> tuple[re.Pattern, dict]:
    groups = {f'r{i}': r for i, r in enumerate(cat['rules'])}
    return re.compile('|'.join(f"(?P<{g}>{r['match']})" for g, r in groups.items()), re.I), groups


def analyze_export(path: Path, cat: dict, owners: list[str]) -> dict:
    head = peek(path) or {}
    rules_rx, groups = _rules(cat)
    objects_rx = re.compile(r'\b((?:' + '|'.join(re.escape(o) for o in owners) + r')_[A-Z0-9_$#]+)\b', re.I) if owners else None
    stats, plugins, uses, hosts, auth, referenced, hits = {}, {}, Counter(), Counter(), Counter(), set(), {}
    where, plugin, in_header, n = 'aplicación', None, True, 0
    app = {'compatibilityMode': None, 'theme': None, 'themeId': None, 'exportType': None}
    with open(path, encoding='utf-8', errors='replace') as f:
        for n, line in enumerate(f, 1):
            if 'varchar2_table(' in line:  # a static file in hexadecimal: most of the export, nothing to read
                continue
            if in_header:
                m = _STAT.match(line)
                if m:
                    stats.setdefault(m.group(1).strip(), int(m.group(2).replace(',', '')))  # the first: pages, then shared components
                m = re.match(r'--\s+Export Type:\s+(.+)', line)
                if m:
                    app['exportType'] = m.group(1).strip()
                in_header = not line.startswith('prompt --application/shared_components') and not line.startswith('prompt --application/pages')
            m = _SECTION.match(line)
            if m:
                where = f'página {int(m.group(2))}' if m.group(2) else f'plug-in {m.group(3)}'
            if '.create_plugin(' in line:
                plugin = {}
            elif plugin is not None:
                a = _ATTR.match(line)
                if a and a.group(1) in ('plugin_type', 'name', 'display_name', 'version_identifier'):
                    plugin[a.group(1)] = a.group(2)
                elif line.startswith(');'):
                    if plugin.get('name'):
                        plugins[plugin['name']] = plugin
                    plugin = None
            a = _ATTR.match(line)
            if a:
                key, value = a.groups()
                if key == 'compatibility_mode':
                    app['compatibilityMode'] = value
                elif key == 'theme_name':
                    app['theme'] = app['theme'] or value
                elif key == 'scheme_type':
                    auth[value] += 1
            else:
                a = _NUM_ATTR.match(line)
                if a and a.group(1) == 'theme_id':
                    app['themeId'] = app['themeId'] or int(a.group(2))
            uses.update(_PLUGIN_USE.findall(line))
            hosts.update(h.lower() for h in _HOST.findall(line))
            if objects_rx:
                referenced.update(x.upper() for x in objects_rx.findall(line))
            for m in rules_rx.finditer(line):
                h = hits.setdefault(groups[m.lastgroup]['id'], {'count': 0, 'locations': Counter()})
                h['count'] += 1
                h['locations'][where] += 1
    by_id = {r['id']: r for r in cat['rules']}
    findings = []
    for rid, h in hits.items():
        r = by_id[rid]
        findings.append({'rule': rid, 'title': r['title'], 'action': r['action'], 'severity': r['severity'], 'status': r['status'], 'since': r['since'],
                         'source': cat['sources'].get(r['source']), 'count': h['count'], 'locations': len(h['locations']),
                         'where': [f'{w} ({c})' for w, c in h['locations'].most_common(MAX_LOCATIONS)]})
    findings.sort(key=lambda x: (SEVERITIES.index(x['severity']), -x['count']))
    if app['exportType'] and 'Application' not in app['exportType']:  # 26.1 refuses page and component exports of earlier releases
        findings.insert(0, {'rule': 'PARTIAL_EXPORT', 'title': 'Export parcial (página o componente)', 'action': 'Volver a exportar la aplicación completa.',
                            'severity': 'BLOCK', 'status': 'DESUPPORTED', 'since': '26.1', 'source': cat['sources']['des261'], 'count': 1, 'locations': 1, 'where': []})
    if app['themeId'] not in (None, 42):
        findings.insert(0, {'rule': 'LEGACY_THEME', 'title': f"Tema antiguo ({app['theme']})", 'action': 'Pasar a Universal Theme (42) antes de subir de versión.',
                            'severity': 'REVIEW', 'status': 'LEGACY', 'since': None, 'source': None, 'count': 1, 'locations': 1, 'where': []})
    counts = Counter(x['severity'] for x in findings)
    pl = [{'name': k, 'displayName': v.get('display_name') or k, 'type': v.get('plugin_type'), 'version': v.get('version_identifier'), 'uses': uses.get(k, 0)}
          for k, v in plugins.items()]
    pl.sort(key=lambda x: -x['uses'])
    return head | app | {'version': short_version(head.get('release')), 'stats': stats, 'plugins': pl, 'findings': findings, 'referenced': sorted(referenced),
                         'authentication': sorted(auth), 'hosts': [h for h, _ in hosts.most_common(15)], 'lines': n,
                         'status': 'BLOCK' if counts['BLOCK'] else 'REVIEW' if counts['REVIEW'] or pl else 'OK',
                         'counts': {s: counts[s] for s in SEVERITIES}}


def ddl_objects(path: Path) -> tuple[str, set]:
    """Objects another .sql creates: ('oracle' | 'other', names). A dump of another engine (MariaDB...) does not define an Oracle schema."""
    names, other = set(), False
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            if 'ENGINE=' in line or line.startswith('/*!'):
                other = True
            m = _DDL.match(line)
            if m:
                names.add(m.group(2).upper())
    return ('other' if other else 'oracle'), names


def analyze(root: Path, log=lambda m: None) -> dict:
    """Every export under root, and how much of the schema it needs the other .sql files define."""
    cat = catalog()
    exports, others = [], []
    for p in sorted(x for x in Path(root).rglob('*') if x.is_file() and x.suffix.lower() in ('.sql', '.txt')):
        (exports if peek(p) else others).append(p)
    owners = sorted({h['owner'] for h in (peek(p) or {} for p in exports) if h.get('owner')})
    apps = []
    for p in exports:
        log(f'analizando {p.name}')
        apps.append(analyze_export(p, cat, owners))
    defined, foreign = set(), []
    for p in others:
        kind, names = ddl_objects(p)
        if kind == 'oracle':
            defined |= names
        elif names:
            foreign.append(p.name)
    for a in apps:
        ref = set(a.pop('referenced'))
        missing = sorted(ref - defined)
        a['schema'] = {'owner': a.get('owner'), 'referenced': len(ref), 'defined': len(ref & defined), 'missing': len(missing), 'missingSample': missing[:MAX_MISSING],
                       'schemas': sorted(o for o in owners if any(x.startswith(o.upper() + '_') for x in ref))}
    totals = Counter()
    for a in apps:
        for k in ('Pages', 'Items', 'Processes', 'Regions', 'Dynamic Actions'):
            totals[k] += a['stats'].get(k, 0)
    return {'apps': apps, 'summary': {'apps': len(apps), 'sourceVersions': sorted({a['version'] for a in apps if a.get('version')}, key=version_key),
                                      'totals': dict(totals), 'plugins': sum(len(a['plugins']) for a in apps),
                                      'blocked': sum(1 for a in apps if a['status'] == 'BLOCK'), 'review': sum(1 for a in apps if a['status'] == 'REVIEW'),
                                      'ddlObjects': len(defined), 'foreignDumps': foreign, 'otherFiles': len(others),
                                      'withSchema': sum(1 for a in apps if a['schema']['referenced'] and not a['schema']['missing'])}}


def hops(source: str, target: str, mode: str = 'yearly', cat: dict | None = None) -> list[str]:
    """Versions to go through, target included. yearly: the last release of each year; all: every release."""
    cat = cat or catalog()
    if target not in cat['versions']:
        raise ValueError(f'Versión de destino desconocida: {target}')
    if version_key(target) <= version_key(source):
        return []
    pool = cat['versions'] if mode == 'all' else cat['yearly']
    return [v for v in pool if version_key(source) < version_key(v) < version_key(target)] + [target]


def plan(apps: list, target: str, mode: str = 'yearly', cat: dict | None = None) -> dict:
    """The jumps and what each one brings: a change belongs to the first jump that reaches its version. Changes already in force in
    the source version, or that depend on no version (internal APIs), come before the first jump."""
    cat = cat or catalog()
    source = min((a['version'] for a in apps if a.get('version')), key=version_key, default=None)
    if source is None:
        return {'source': None, 'target': target, 'mode': mode, 'hops': [], 'baseline': []}

    def grouped(selected):
        rows = {}
        for a in apps:
            for f in a['findings']:
                if selected(f):
                    r = rows.setdefault(f['rule'], {k: f[k] for k in ('rule', 'title', 'action', 'severity', 'status', 'since', 'source')} | {'count': 0, 'apps': []})
                    r['count'] += f['count']
                    r['apps'].append(a.get('name') or a.get('file'))
        return sorted(rows.values(), key=lambda x: (SEVERITIES.index(x['severity']), -x['count']))
    out, prev = [], source
    for v in hops(source, target, mode, cat):
        lo, hi = version_key(prev), version_key(v)
        items = grouped(lambda f: f['since'] and lo < version_key(f['since']) <= hi)
        out.append({'version': v, 'from': prev, 'changes': items, 'counts': {s: sum(1 for x in items if x['severity'] == s) for s in SEVERITIES}})
        prev = v
    return {'source': source, 'target': target, 'mode': mode, 'hops': out,
            'baseline': grouped(lambda f: not f['since'] or version_key(f['since']) <= version_key(source))}


def markdown(result: dict, plan_: dict) -> str:
    s = result['summary']
    out = ['# Análisis de aplicaciones Oracle APEX', '', f"{s['apps']} aplicaciones · versión de origen {', '.join(s['sourceVersions']) or '—'} · "
           f"{s['totals'].get('Pages', 0)} páginas · {s['plugins']} plug-ins instalados", '',
           '| Aplicación | Dueño | Versión | Páginas | Plug-ins | Bloquean | Revisar | Objetos con DDL | Estado |', '|---|---|---|---:|---:|---:|---:|---|---|']
    for a in result['apps']:
        c, sc = a['counts'], a['schema']
        out.append(f"| {a.get('appId')} · {a.get('name')} | {a.get('owner')} | {a.get('version')} | {a['stats'].get('Pages', 0)} | {len(a['plugins'])} | "
                   f"{c['BLOCK']} | {c['REVIEW']} | {sc['defined']} de {sc['referenced']} | {a['status']} |")
    out += ['', f"## Plan de saltos: {plan_['source']} → {plan_['target']}", '']
    for h in plan_['hops']:
        out += [f"### {h['from']} → {h['version']}", '']
        if not h['changes']:
            out += ['Sin cambios detectados en estas aplicaciones.', '']
        out += [f"- **{c['severity']}** {c['title']} ({c['count']} usos en {len(c['apps'])} aplicaciones). {c['action']}" for c in h['changes']]
        out.append('')
    if plan_['baseline']:
        out += ['## Revisar desde el origen', '', 'Ya aplican en la versión de origen o no dependen de una versión.', '']
        out += [f"- **{c['severity']}** {c['title']} ({c['count']} usos). {c['action']}" for c in plan_['baseline']]
    return '\n'.join(out) + '\n'

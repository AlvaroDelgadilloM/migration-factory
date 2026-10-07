"""Code-smell and architecture detectors + quality metrics (quality-before/after.json). Facts and locations only."""
import json
import re
from functools import lru_cache
from pathlib import Path

from ..analysis.scanner import INTEGRATION_TYPES, scan
from ..config import REPO_ROOT
from . import java

RULES_PATH = REPO_ROOT / 'rules' / 'modernization-rules.json'
INJECT = {'Autowired', 'Inject'}
TIMEOUT = re.compile(r'(?i)(connectTimeout|socketTimeout|connectionRequestTimeout|responseTimeout|receiveTimeout|requestTimeout|timeout)=')
KIND = {c: t for t, cs in INTEGRATION_TYPES.items() for c in cs}


@lru_cache
def load_rules(path: str | None = None) -> dict:
    data = json.loads(Path(path or RULES_PATH).read_text(encoding='utf-8'))
    for r in data['rules']:
        assert r['tier'] in ('SAFE', 'REFACTOR', 'ARCHITECTURE') and r['level'] in ('AUTO', 'AUTO_TEST', 'REVIEW', 'MANUAL'), r['id']
        assert r['tier'] != 'ARCHITECTURE' or r['mechanism'] == 'proposal', r['id']  # ARCHITECTURE is never applied
    data['byId'] = {r['id']: r for r in data['rules']}
    return data


def java_files(root: Path):
    for f in sorted(root.rglob('*.java')):
        rel = f.relative_to(root).as_posix()
        if '/src/main/java/' in '/' + rel and not any(p in ('target', '.git') for p in f.parts):
            yield f, rel


def field_injection_candidates(parsed) -> list[dict]:
    """Classes whose injected fields can move to a constructor; reasons when they cannot (never guessed)."""
    out = []
    for t in parsed['types']:
        inj = [mm for mm in t['members'] if mm['kind'] == 'field' and INJECT & set(mm['annotations']) and 'static' not in mm['modifiers']]
        if not inj:
            continue
        why = []
        if t['kind'] != 'class' or t['abstract']:
            why.append('tipo abstracto o no clase')
        if any(mm['kind'] == 'method' and mm['name'] == t['name'] for mm in t['members']):
            why.append('ya declara constructor(es)')
        if any(set(f['annotations']) - INJECT or f['multi'] or f['initialized'] or 'final' in f['modifiers'] for f in inj):
            why.append('campo con otras anotaciones, varias variables o inicializado')
        out.append({'type': t, 'fields': inj, 'blockedBy': why})
    return out


def analyze(root: Path, target: str = 'spring') -> dict:
    rules = load_rules()
    th = rules['thresholds']
    report = scan(root, target)
    items, metrics = [], {'javaFiles': 0, 'javaLines': 0, 'classes': 0, 'routeBuilders': len(report['routeBuilders']),
                          'routes': len(report['routes']), 'testClasses': report['testClasses']}

    def add(rule, file, line, detail, suggestion=''):
        items.append({'rule': rule, 'file': file, 'line': line, 'detail': detail, 'suggestion': suggestion})

    max_cfg = 0
    builders_without_errors, on_exc = [], {}
    for f, rel in java_files(root):
        src = f.read_text(encoding='utf-8', errors='replace')
        metrics['javaFiles'] += 1
        metrics['javaLines'] += sum(1 for line in src.splitlines() if line.strip())
        try:
            parsed = java.parse(src)
        except Exception:  # unreadable file: counted, never guessed
            add('PARSE_ERROR', rel, 1, 'no se pudo leer la estructura del archivo')
            continue
        m = java.mask(src)
        for c in field_injection_candidates(parsed):
            names = ', '.join(x['name'] for x in c['fields'])
            add('MOD_FIELD_INJECTION', rel, c['fields'][0]['line'], f"{c['type']['name']}: {len(c['fields'])} campo(s) inyectado(s) ({names})"
                + (f" — no transformable: {'; '.join(c['blockedBy'])}" if c['blockedBy'] else ''),
                f"public {c['type']['name']}({', '.join(x['type'] + ' ' + x['name'] for x in c['fields'])}) {{ ... }}")
            items[-1]['class'] = c['type']['name']
        for t in parsed['types']:
            metrics['classes'] += 1
            members = t['members']
            values = [(mm, re.search(r'@Value\s*\(\s*"\$\{([\w.\-]+)', mm['text'])) for mm in members if mm['kind'] == 'field' and 'Value' in mm['annotations']]
            prefixes = {}
            for mm, v in values:
                if v and '.' in v[1]:
                    prefixes.setdefault(v[1].rsplit('.', 1)[0], []).append(mm)
            for p, ms in prefixes.items():
                if len(ms) >= 2:
                    add('MOD_CONFIG_PROPERTIES', rel, ms[0]['line'], f'{len(ms)} @Value con prefijo "{p}" en {t["name"]}',
                        f'@ConfigurationProperties(prefix = "{p}") record {t["name"]}Properties(...)')
            fields = [mm for mm in members if mm['kind'] == 'field' and 'static' not in mm['modifiers']]
            methods = [mm for mm in members if mm['kind'] == 'method' and mm['name'] != t['name']]
            if (t['kind'] == 'class' and fields and not t['extends'] and not t['abstract'] and not t['annotations']
                    and all('private' in x['modifiers'] and 'final' in x['modifiers'] for x in fields)
                    and all(re.match(r'(get|is)[A-Z]|equals$|hashCode$|toString$', x['name']) for x in methods)):
                add('MOD_RECORD_DTO', rel, t['line'], f'{t["name"]}: {len(fields)} campo(s) final(es) y solo getters',
                    f'public record {t["name"]}({", ".join(x["type"] + " " + x["name"] for x in fields)}) {{}}')
            for mm in members:
                if mm['kind'] != 'method' or 'bodyStart' not in mm:
                    continue
                body = m[mm['bodyStart']:mm['bodyEnd'] + 1]
                for c in re.finditer(r'catch\s*\([^)]*\)\s*\{\s*\}', body):
                    add('MOD_EMPTY_CATCH', rel, java.line_of(m, mm['bodyStart'] + c.start()), f'catch vacío en {t["name"]}.{mm["name"]}')
                if mm['name'] == 'configure' and t['extends'] and 'RouteBuilder' in t['extends']:
                    max_cfg = max(max_cfg, mm['bodyLines'])
                    if mm['bodyLines'] > th['configureLines']:
                        add('MOD_LARGE_ROUTE', rel, mm['line'], f'{t["name"]}.configure(): {mm["bodyLines"]} líneas (umbral {th["configureLines"]})',
                            'dividir en sub-rutas direct: por responsabilidad')
                    for p in re.finditer(r'\.process\s*\(\s*(?:\w+\s*->|\(\s*\w*\s*\)\s*->|new\s+Processor\s*\(\s*\))\s*\{', body):
                        ob = mm['bodyStart'] + p.end() - 1
                        lines = java.line_of(m, java.match_brace(m, ob)) - java.line_of(m, ob) + 1
                        if lines >= th['processorLines']:
                            add('MOD_LOGIC_IN_ROUTE', rel, java.line_of(m, ob), f'Processor en línea de {lines} líneas en {t["name"]}',
                                '@Component class XxxProcessor implements Processor { ... } y .process(xxxProcessor)')
                    if not re.search(r'\b(onException|errorHandler)\s*\(', body):
                        builders_without_errors.append((rel, mm['line'], t['name']))
                    for e in re.finditer(r'onException\s*\(([^)]*)\)', body):
                        on_exc.setdefault(re.sub(r'\s+', '', e[1]), []).append((rel, java.line_of(m, mm['bodyStart'] + e.start())))
                elif mm['bodyLines'] > th['methodLines']:
                    add('MOD_LONG_METHOD', rel, mm['line'], f'{t["name"]}.{mm["name"]}(): {mm["bodyLines"]} líneas (umbral {th["methodLines"]})')
    metrics['maxConfigureLines'] = max_cfg
    # the no-arg constructor disappears with constructor injection: callers anywhere (main or test) make it not transformable
    sources = {f.relative_to(root).as_posix(): java.mask(f.read_text(encoding='utf-8', errors='replace'))
               for f in root.rglob('*.java') if not any(p in ('target', '.git') for p in f.parts)}
    for it in items:
        if it['rule'] == 'MOD_FIELD_INJECTION' and 'no transformable' not in it['detail']:
            new = re.compile(r'\bnew\s+(?:[\w.]+\.)?' + re.escape(it['class']) + r'\s*\(\s*\)')
            calls = [f'{rel}:{java.line_of(src, m.start())}' for rel, src in sorted(sources.items()) for m in new.finditer(src)]
            if calls:
                it['detail'] += ' — no transformable: constructor sin argumentos usado en ' + ', '.join(calls[:5])
                it['suggestion'] += ' y actualizar esas llamadas (o declarar las rutas como @Component)'
    if builders_without_errors and metrics['routeBuilders'] > 1:
        for rel, line, name in builders_without_errors:
            add('MOD_ERROR_HANDLING', rel, line, f'{name} sin onException/errorHandler propio ni centralizado',
                'RouteConfigurationBuilder con onException comunes, aplicado a todas las rutas')
    for exc, locs in on_exc.items():
        if len(locs) > 1:
            add('MOD_ERROR_HANDLING', locs[0][0], locs[0][1], f'onException({exc}) repetido en {len(locs)} RouteBuilder', 'centralizar en RouteConfigurationBuilder')
    for r in report['routes']:
        eps = r['endpoints']
        if len(eps) > th['endpointsPerRoute']:
            add('MOD_LARGE_ROUTE', r['file'], r['line'], f"ruta {r['routeId'] or '?'}: {len(eps)} endpoints (umbral {th['endpointsPerRoute']})")
        types = {KIND.get(e['component']) for e in eps if e['kind'] not in ('from', 'fromF')}
        if 'DB' in types and 'JMS' in types:
            add('MOD_OUTBOX', r['file'], r['line'], f"ruta {r['routeId'] or '?'}: escribe en BD y publica en JMS",
                'Outbox: insertar el evento en la misma transacción de BD y publicarlo desde una ruta separada')
        for e in eps:
            if e['kind'] in ('from', 'fromF') or e['dynamic'] or not e['uri']:
                continue
            t = KIND.get(e['component'])
            if t in ('REST/HTTP', 'SOAP', 'REST_CXF') and not TIMEOUT.search(e['uri']):
                add('MOD_RESILIENCE', e['file'], e['line'], f"{e['component']} sin timeout explícito: {e['uri'][:80]}",
                    'definir connect/response timeout, reintentos acotados y circuitBreaker() con fallback')
    for f in report['findings']:
        if f['ruleId'] == 'HARDCODED_SECRET':
            add('MOD_SECRETS', f['file'], f['line'], 'valor secreto literal')
    http = [(r, e) for r in report['routes'] for e in r['endpoints']
            if e['component'] in ('http', 'https') and not e['dynamic'] and e['uri'] and '{{' not in e['uri'] and '${' not in e['uri']]
    for r, e in http:
        add('MOD_HARDCODED_CONFIG', e['file'], e['line'], f"URL literal {e['uri'][:80]}", 'services.<host>.url en application.yml')
    counts = {}
    for i in items:
        counts[i['rule']] = counts.get(i['rule'], 0) + 1
    metrics['findingsByRule'] = dict(sorted(counts.items()))
    metrics['findings'] = len(items)
    return {'items': items, 'metrics': metrics, 'scan': report, 'httpEndpoints': [e for _, e in http]}

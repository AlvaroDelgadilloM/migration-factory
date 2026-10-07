"""Static inventory only: never executes Maven or project code and never writes into the source tree.

Output distinguishes *observed* values (literal text in the repository) from *resolved* values
(after local property/parent/BOM resolution). Java DSL routes are found by regular expression and
are explicitly approximate; XML DSL routes come from a real XML parse.
"""
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from . import maven, xmlparse
from ..baseline.vendor_bom_analyzer import VENDOR_GROUPS, detect_platform

SCHEMA_VERSION = 2
IGNORED = {'.git', 'target', 'build', 'node_modules', '.venv', 'vendor', '.idea', '.mvn'}
EXTENSIONS = {'.java', '.xml', '.properties', '.yml', '.yaml'}
MAX_FILE_BYTES = 2_000_000
MAX_FILES = 50_000
COMPAT_PATH = Path(__file__).resolve().parents[3] / 'rules' / 'camel' / 'camel-3-to-4-components.json'
CATALOG_PATH = Path(os.environ.get('MF_RULES_CATALOG', Path(__file__).resolve().parents[3] / 'rules' / 'catalog.json'))
SEVERITY_ORDER = {'high': 0, 'medium': 1, 'low': 2, 'info': 3}

SECRET_PARAM = re.compile(r'pass(word)?|secret|token|key|credential', re.I)
SECRET_VALUE = re.compile(r'(?i)((?:password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*["\']?)[^\s"\']+')
INTEGRATION_TYPES = {
    'REST/HTTP': {'http', 'https', 'http4', 'https4', 'rest', 'servlet', 'undertow', 'platform-http', 'netty-http', 'jetty'},
    'SOAP': {'cxf', 'spring-ws'},
    'REST_CXF': {'cxfrs'},
    'JMS': {'jms', 'activemq', 'sjms', 'sjms2', 'amqp', 'hornetq'},
    'DB': {'sql', 'jdbc', 'jpa', 'mybatis', 'sql-stored'},
    'FILES': {'file', 'ftp', 'ftps', 'sftp'},
    'SCHEDULING': {'quartz', 'quartz2', 'timer', 'scheduler', 'cron'},
    'MAIL': {'smtp', 'smtps', 'imap', 'imaps', 'pop3', 'pop3s'},
    'INTER_CONTEXT': {'vm', 'direct-vm'},
    'MESSAGING': {'kafka', 'rabbitmq', 'mqtt'},
}
# A literal endpoint is a single string literal closing the call; anything else is dynamic.
_ARG = r'\s*\(\s*(?:("(?:[^"\\\n]|\\.)*")\s*[,)]|([^)\n]*))'
JAVA_FROM = re.compile(r'\bfrom(F?)' + _ARG)
JAVA_ENDPOINT = re.compile(r'\.(to|toD|toF|enrich|pollEnrich|wireTap|inOnly|inOut|recipientList)' + _ARG)
JAVA_ROUTE_ID = re.compile(r'\.routeId\s*\(\s*"([^"\n]+)"')
JAVA_BEAN = re.compile(r'\.(?:bean|beanRef)\s*\(')
JAVA_PACKAGE = re.compile(r'^\s*package\s+([\w.]+)\s*;', re.M)
JAVA_ROUTE_BUILDER = re.compile(r'\b((?:abstract\s+)?)class\s+(\w+)\s+extends\s+(?:Endpoint)?RouteBuilder\b')


def java_route_builders(rel, code):
    """RouteBuilder subclasses (regex over comment-stripped code) and whether a no-arg constructor is usable."""
    pkg = JAVA_PACKAGE.search(code)
    out = []
    for m in JAVA_ROUTE_BUILDER.finditer(code):
        if m[1]:
            continue  # abstract
        ctors = re.findall(r'\b(?:public|protected|private)?\s*' + m[2] + r'\s*\(([^)]*)\)\s*(?:throws[^{]*)?\{', code)
        ctxp = re.search(r'\.contextPath\s*\(\s*"([^"\n]+)"', code)
        out.append({'class': m[2], 'package': pkg[1] if pkg else '', 'file': rel, 'line': _line(code, m.start()),
                    'noArgConstructor': not ctors or any(not c.strip() for c in ctors),
                    'restContextPath': ctxp[1] if ctxp else None})
    return out
XML_ENDPOINT_TAGS = {'from', 'to', 'toD', 'enrich', 'pollEnrich', 'wireTap', 'inOnly', 'inOut'}


def load_catalog(path=None):
    data = json.loads(Path(path or CATALOG_PATH).read_text(encoding='utf-8'))
    return catalog_from_rules(data['catalogVersion'], data['rules'])


def catalog_from_rules(version, rules):
    rules = [dict(r) for r in rules]
    for r in rules:
        if r.get('pattern'):
            r['_re'] = re.compile(r['pattern'])
        if r.get('excludeLine'):
            r['_exclude'] = re.compile(r['excludeLine'])
    digest = hashlib.sha256(json.dumps([{k: v for k, v in r.items() if not k.startswith('_')} for r in rules], sort_keys=True, default=str).encode()).hexdigest()
    return {'catalogVersion': version, 'rules': rules, 'digest': digest}


def redact_uri(uri: str) -> str:
    """Remove userinfo and secret-looking query parameters from an endpoint URI."""
    uri = re.sub(r'(//)[^/@\s]+@', r'\1***@', uri)
    if '?' in uri:
        base, q = uri.split('?', 1)
        pairs = [(k, '***' if SECRET_PARAM.search(k) else v) for k, v in parse_qsl(q, keep_blank_values=True)]
        uri = base + '?' + urlencode(pairs, safe='*{}$:/')
    return uri


def strip_java_comments(src: str) -> str:
    """Blank out // and /* */ comments while keeping string literals and line numbers."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"' and src[j] != '\n':
                j += 2 if src[j] == '\\' else 1
            out.append(src[i:j + 1]); i = j + 1
        elif src.startswith('//', i):
            j = src.find('\n', i)
            j = n if j < 0 else j
            out.append(' ' * (j - i)); i = j
        elif src.startswith('/*', i):
            j = src.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r'[^\n]', ' ', src[i:j])); i = j
        else:
            out.append(c); i += 1
    return ''.join(out)


def strip_xml_comments(src: str) -> str:
    return re.sub(r'<!--.*?-->', lambda m: re.sub(r'[^\n]', ' ', m[0]), src, flags=re.S)


def _endpoint(raw: str, file, line, kind):
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        uri = raw[1:-1]
        dynamic = '${' in uri or kind in ('toD', 'toF', 'recipientList')
    else:
        uri, dynamic = None, True
    m = re.match(r'([\w+-]+):', uri or '')
    return {'component': m[1] if m else None, 'uri': redact_uri(uri) if uri else None, 'dynamic': dynamic,
            'kind': kind, 'file': file, 'line': line}


def _line(content, pos):
    return content.count('\n', 0, pos) + 1


def java_routes(rel, content):
    code = strip_java_comments(content)
    starts = [m for m in JAVA_FROM.finditer(code)]
    routes = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(code)
        block = code[m.start():end]
        line = _line(code, m.start())
        src = _endpoint(m[2] or m[3], rel, line, 'fromF' if m[1] else 'from')
        rid = JAVA_ROUTE_ID.search(block)
        eps = [src] + [_endpoint(e[2] or e[3], rel, _line(code, m.start() + e.start()), e[1]) for e in JAVA_ENDPOINT.finditer(block)]
        eps += [{'component': 'bean', 'uri': None, 'dynamic': False, 'kind': 'bean', 'file': rel, 'line': _line(code, m.start() + b.start())}
                for b in JAVA_BEAN.finditer(block)]
        routes.append({'key': f'{rel}:{line}', 'routeId': rid[1] if rid else None, 'dsl': 'java', 'file': rel, 'line': line,
                       'detection': 'java-regex-approximate', 'fromUri': src['uri'], 'dynamicFrom': src['dynamic'],
                       'endpoints': eps})
    return routes, code


def xml_routes(rel, tree):
    routes = []
    for node in tree.iter():
        if node.tag != 'route':
            continue
        eps = []
        for e in node.iter():
            if e.tag in XML_ENDPOINT_TAGS and 'uri' in e.attrib:
                eps.append(_endpoint('"' + e.attrib['uri'] + '"', rel, e.line, 'toD' if e.tag == 'toD' else e.tag))
        src = next((e for e in eps if e['kind'] == 'from'), None)
        routes.append({'key': f'{rel}:{node.line}', 'routeId': node.attrib.get('id'), 'dsl': 'xml', 'file': rel, 'line': node.line,
                       'detection': 'xml-parsed', 'fromUri': src and src['uri'], 'dynamicFrom': bool(src and src['dynamic']),
                       'endpoints': eps})
    return routes


def iter_files(root: Path, errors: list):
    """Walk without following symlinks; skip ignored directories."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        dirnames[:] = sorted(n for n in dirnames if n not in IGNORED and not (d / n).is_symlink())
        for name in sorted(filenames):
            f = d / name
            rel = f.relative_to(root).as_posix()
            if f.is_symlink():
                errors.append({'file': rel, 'code': 'SYMLINK_IGNORED'})
                continue
            if f.suffix not in EXTENSIONS:
                continue
            count += 1
            if count > MAX_FILES:
                errors.append({'file': rel, 'code': 'MAX_FILES_EXCEEDED'})
                return
            yield f, rel


def scan(root, target='spring', catalog=None):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('Source must be a directory')
    if target not in {'spring', 'quarkus'}:
        raise ValueError('Unsupported target')
    catalog = catalog or load_catalog()
    text_rules = [r for r in catalog['rules'] if r['detector'] == 'text']
    report = {'schemaVersion': SCHEMA_VERSION, 'target': target, 'catalogVersion': catalog['catalogVersion'],
              'catalogDigest': catalog['digest'], 'analysis': 'static', 'modules': [], 'routes': [],
              'components': [], 'findings': [], 'errors': [], 'routeBuilders': [], 'testClasses': 0, 'usage': {}, 'usageEvidence': {}}
    tokens = json.loads(COMPAT_PATH.read_text(encoding='utf-8'))['usageTokens'] if COMPAT_PATH.exists() else []
    findings = {}

    def add_finding(rule, rel, line, evidence):
        f = _finding(rule, rel, line, evidence)
        findings.setdefault(f['fingerprint'], f)  # one finding per rule/file/line, never double counted

    poms = {}
    for f, rel in iter_files(root, report['errors']):
        if f.stat().st_size > MAX_FILE_BYTES:
            report['errors'].append({'file': rel, 'code': 'FILE_TOO_LARGE'})
            continue
        try:
            content = f.read_text(encoding='utf-8')
        except (UnicodeError, OSError):
            report['errors'].append({'file': rel, 'code': 'READ_ERROR'})
            continue
        tree, matchable = None, content
        if f.suffix == '.xml':
            matchable = strip_xml_comments(content)
            try:
                tree = xmlparse.parse(content)
            except xmlparse.XmlRejected:
                report['errors'].append({'file': rel, 'code': 'XML_DTD_REJECTED'})
            except ValueError:
                report['errors'].append({'file': rel, 'code': 'INVALID_XML'})
        if f.name == 'pom.xml' and tree is not None:
            try:
                poms[rel] = maven.read_pom(f, rel)
            except ValueError:
                report['errors'].append({'file': rel, 'code': 'INVALID_POM'})
        if f.suffix == '.java':
            routes, matchable = java_routes(rel, content)
            report['routes'] += routes
            report['routeBuilders'] += java_route_builders(rel, matchable)
            if '/src/test/' in '/' + rel and re.search(r'@Test\b', matchable):
                report['testClasses'] += 1
        if tree is not None:
            report['routes'] += xml_routes(rel, tree)
        for tok in tokens:  # doc 19: how components are used decides their Camel 4 replacement
            if tok in matchable or ('/' in tok and f'/{tok}/' in f'/{rel}'):
                report['usage'].setdefault(tok, [])
                if len(report['usage'][tok]) < 20:
                    report['usage'][tok].append(rel)
                    # doc 34: evidence with file and line (first occurrence; path tokens have no line)
                    pos = matchable.find(tok)
                    report['usageEvidence'].setdefault(tok, []).append({'file': rel, 'line': matchable.count('\n', 0, pos) + 1 if pos >= 0 else None})
        for n, line in enumerate(matchable.splitlines(), 1):
            for rule in text_rules:
                if rule.get('_exclude') and rule['_exclude'].search(line):
                    continue
                m = rule['_re'].search(line)
                if m:
                    add_finding(rule, rel, n, SECRET_VALUE.sub(r'\1***', redact_uri(m[0])))

    report['modules'] = maven.resolve(root, poms)
    report['findings'] = list(findings.values())
    return finalize(report, catalog)


def _finding(rule, rel, line, evidence):
    return {'fingerprint': f"{rule['id']}:{rel}:{line}", 'ruleId': rule['id'], 'ruleVersion': rule['version'], 'file': rel, 'line': line,
            'category': rule.get('category', 'MIGRATION'),
            'severity': rule['severity'], 'classification': rule['classification'], 'blocking': rule.get('blocking', False),
            'recommendation': rule['recommendation'], 'evidence': evidence[:120], 'status': 'open'}


def finalize(report, catalog):
    """(Re)compute Maven-derived findings from current module data, components and summary."""
    rules_by_id = {r['id']: r for r in catalog['rules']}
    findings = {f['fingerprint']: f for f in report['findings'] if rules_by_id.get(f['ruleId'], {}).get('detector') != 'maven'}

    def add(rule_id, rel, line, evidence):
        if rule_id in rules_by_id:
            f = _finding(rules_by_id[rule_id], rel, line, evidence)
            findings.setdefault(f['fingerprint'], f)

    for mod in report['modules']:
        camel2 = [d for d in mod['dependencies'] if d['groupId'].startswith('org.apache.camel') and re.match(r'2\.', d['versionResolved'] or '')]
        if camel2:  # one finding per module: it is one migration concern, not one per artifact
            add('CAMEL2_VERSION', mod['file'], camel2[0]['line'], ', '.join(f"{d['artifactId']}:{d['versionResolved']}" for d in camel2))
        if mod['packaging'] in ('ear', 'war'):
            add(f"PACKAGING_{mod['packaging'].upper()}", mod['file'], mod['packagingLine'] or 1, mod['packaging'])
        prefix = mod['file'][:-len('pom.xml')]
        osgi = [f for f in findings.values() if f['ruleId'] in ('OSGI_BUNDLE', 'BLUEPRINT') and f['file'].startswith(prefix)]
        vendor = [b for b in mod.get('boms', []) if b.split(':')[0] in VENDOR_GROUPS]  # doc 42: Fuse/Karaf platform BOM
        if mod['packaging'] == 'bundle' or osgi or vendor:  # doc 27: OSGi/Karaf runtime, not just Camel
            signals = sorted({'packaging=bundle'} if mod['packaging'] == 'bundle' else set()) + sorted({f['ruleId'] for f in osgi}) \
                + [f'vendor BOM {b}' for b in vendor]
            add('TARGET_RUNTIME_MIGRATION_REQUIRED', mod['file'], mod['packagingLine'] or 1,
                f"OSGI_BLUEPRINT → {report.get('target', 'spring')}: {', '.join(signals)}")
        java = mod.get('javaResolved') or mod.get('javaObserved') or ''
        m = re.match(r'(?:1\.)?(\d+)', java)
        if m and int(m[1]) < 17:
            add('JAVA_LEGACY', mod['file'], 1, f'Java {java}')
        if mod['unresolved'] or any(d['versionSource'] == 'unresolved' for d in mod['dependencies']):
            add('MAVEN_UNRESOLVED', mod['file'], 1, '; '.join(mod['unresolved'])[:120] or 'versiones sin resolver')
    builders = report.get('routeBuilders', [])
    tests = report.get('testClasses', 0)
    if builders and tests < len(builders):  # rule: at least one test class per RouteBuilder
        add('TEST_COVERAGE', builders[0]['file'], 1, f'{tests} clase(s) de prueba para {len(builders)} RouteBuilder')
    report['components'] = sorted({e['component'] for r in report['routes'] for e in r['endpoints'] if e['component']})
    report['platform'] = detect_platform(report['modules'], report.get('usage'))  # doc 42 PlatformDetector (static)
    kind = {c: t for t, cs in INTEGRATION_TYPES.items() for c in cs}
    report['integrations'] = [{'type': kind[e['component']], 'component': e['component'], 'uri': e['uri'], 'dynamic': e['dynamic'],
                               'direction': 'consumer' if e['kind'] in ('from', 'fromF') else 'producer',
                               'file': e['file'], 'line': e['line'], 'routeId': r['routeId']}
                              for r in report['routes'] for e in r['endpoints'] if e['component'] in kind]
    report['findings'] = sorted(findings.values(), key=lambda x: (SEVERITY_ORDER[x['severity']], x['file'], x['line'], x['ruleId']))
    sev = {}
    for x in report['findings']:
        sev[x['severity']] = sev.get(x['severity'], 0) + 1
    report['summary'] = {
        'modules': len(report['modules']),
        'routesXmlParsed': sum(r['dsl'] == 'xml' for r in report['routes']),
        'routesJavaApproximate': sum(r['dsl'] == 'java' for r in report['routes']),
        'dynamicEndpoints': sum(e['dynamic'] for r in report['routes'] for e in r['endpoints']),
        'findings': len(report['findings']), 'findingsBySeverity': sev,
        'integrationsByType': {t: sum(i['type'] == t for i in report['integrations']) for t in sorted({i['type'] for i in report['integrations']})},
        'errors': len(report['errors']),
        'testClasses': report.get('testClasses', 0),
        'usage': report.get('usage', {}),
        'usageEvidence': report.get('usageEvidence', {}),
        'platform': report['platform'],
    }
    from .semantic import enrich  # improvements/02: graph, migration action per finding, risks, hints
    return enrich(report, kind)


def apply_effective(report, effective: dict, catalog):
    """Merge `mvn help:effective-pom` results: resolved values replace unresolved/static ones, with provenance."""
    for mod in report['modules']:
        eff = effective.get((mod['groupId'], mod['artifactId']))
        if not eff:
            continue
        for d in mod['dependencies']:
            v = eff['deps'].get((d['groupId'], d['artifactId']))
            if v:
                d['versionStatic'] = d['versionResolved']
                d['versionResolved'], d['versionSource'] = v, 'maven-effective'
        if eff['java']:
            mod['javaResolved'] = eff['java']  # javaSource keeps describing where the observed value came from
        mod['camelVersions'] = sorted({d['versionResolved'] or '?' for d in mod['dependencies'] if d['groupId'].startswith('org.apache.camel')})
        mod['staticUnresolved'], mod['unresolved'] = mod['unresolved'], []
        mod['resolution'] = 'maven-effective'
    return finalize(report, catalog)

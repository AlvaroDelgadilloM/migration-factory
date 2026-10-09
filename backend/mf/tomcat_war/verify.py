"""Equivalence evidence for the `tomcat-war` profile (gate G4, docs/16_EQUIVALENCIA.md).

Compiling and starting prove little: Camel 4 builds some blocks differently without any error. Two checks compare the
migrated code against the ORIGINAL code running on its original Camel version, with no access to the client's servers:

  routes     structure of every route: configure() of each RouteBuilder is executed on both versions (nothing is
             started) and the resulting model trees are compared.
  responses  the same HTTP request is sent to the original code (baseline server: Camel 2 + Jetty) and to the migrated
             WAR, and status, Content-Type and body are compared.

Both need a JDK (17+), Maven and the migrated workspace already built (`migrate --build`). Stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from . import blueprint, pipeline

VERIFY_DIR = pipeline.RULES_DIR / 'verify'
OPENS = ['java.base/java.lang', 'java.base/java.util', 'java.base/java.lang.reflect', 'java.base/java.net', 'java.base/java.io',
         'java.base/sun.nio.ch', 'java.xml/com.sun.org.apache.xerces.internal.jaxp', 'java.base/java.text']


class ToolError(pipeline.InvalidInput):
    pass


# --- tools ------------------------------------------------------------------------------------------------------------
def _java(tool: str) -> str:
    home = os.environ.get('JAVA_HOME')
    exe = (Path(home) / 'bin' / tool) if home else None
    found = shutil.which(str(exe)) if exe else None
    found = found or shutil.which(tool)
    if not found:
        raise ToolError(f'No se encontró {tool}: defina JAVA_HOME con un JDK 17 o superior')
    return found


def _env(classpath: list) -> dict:
    """The classpath travels in CLASSPATH: on the command line it would exceed the Windows limit, and javac does not
    expand `dir/*` wildcards inside an @argfile."""
    return os.environ | {'CLASSPATH': os.pathsep.join(str(c) for c in classpath)}


def _run(cmd: list, log_file: Path, cwd: Path | None = None, timeout: int = 1800, classpath: list | None = None) -> int:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open('ab') as fh:
        fh.write((' '.join(str(c) for c in cmd)[:2000] + '\n').encode())
        return subprocess.run([str(c) for c in cmd], cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout,
                              env=_env(classpath) if classpath is not None else None).returncode


def camel2_version(projects: list[Path]) -> str:
    versions = Counter()
    for p in projects:
        m = re.search(r'<camel\.version>\s*(2\.[\d.]+)\s*<', pipeline.read(p / 'pom.xml'))
        if m:
            versions[m.group(1)] += 1
    if not versions:
        raise ToolError('No se pudo determinar la versión de Camel 2 del origen (<camel.version> en los pom.xml)')
    return versions.most_common(1)[0][0]


def baseline_libs(work: Path, version: str, log=lambda m: None) -> Path:
    """Camel 2 libraries the original bundles compile against (resolved once per work directory)."""
    lib = work / 'baseline' / 'lib'
    marker = work / 'baseline' / f'.resolved-{version}'
    if marker.exists() and any(lib.glob('*.jar')):
        return lib
    mvn = shutil.which('mvn')
    if not mvn:
        raise ToolError('mvn no está en el PATH')
    pom = work / 'baseline' / 'pom.xml'
    pom.parent.mkdir(parents=True, exist_ok=True)
    pom.write_text((VERIFY_DIR / 'baseline-pom.xml').read_text(encoding='utf-8').replace('@CAMEL2_VERSION@', version), encoding='utf-8')
    log(f'resolviendo las librerías de Camel {version} (línea base)')
    if _run([mvn, '-B', '-q', '-f', pom, 'dependency:copy-dependencies', '-DoutputDirectory=lib'], work / 'logs' / 'baseline-libs.log') != 0:
        raise ToolError(f"No se pudieron resolver las librerías de la línea base; ver {work / 'logs' / 'baseline-libs.log'}")
    marker.write_text('ok')
    return lib


def _compile(sources: list[Path], classpath: list, out: Path, release: str, log_file: Path, encoding: str = 'UTF-8') -> bool:
    out.mkdir(parents=True, exist_ok=True)
    args = out.parent / f'{out.name}.sources'
    args.write_text('\n'.join('"' + str(s).replace('\\', '/') + '"' for s in sources), encoding='utf-8')
    return _run([_java('javac'), '--release', release, '-nowarn', '-encoding', encoding, '-proc:none', '-d', out, f'@{args}'], log_file,
                classpath=classpath) == 0


# --- module graph (who needs whose classes) -----------------------------------------------------------------------------
def module_dependencies(sources: dict) -> dict:
    """sources: module -> java source dir. A module depends on another when its sources mention one of the other's packages
    (import or a class name in a string, e.g. Class.forName) that it does not declare itself."""
    packages, text = {}, {}
    for m, d in sources.items():
        files = sorted(d.rglob('*.java')) if d.is_dir() else []
        text[m] = '\n'.join(pipeline.read(f) for f in files)
        packages[m] = set(re.findall(r'^\s*package\s+([\w.]+)\s*;', text[m], re.M))
    deps = {}
    for m in sources:
        deps[m] = sorted(o for o in sources if o != m and any(re.search(r'\b' + re.escape(p) + r'\.[A-Z]', text[m]) for p in packages[o] - packages[m]))
    return deps


def _order(deps: dict) -> list:
    done, out = set(), []

    def visit(m, stack=()):
        if m in done or m in stack:
            return
        for d in deps[m]:
            visit(d, stack + (m,))
        done.add(m)
        out.append(m)
    for m in sorted(deps):
        visit(m)
    return out


def _closure(m: str, deps: dict) -> list:
    seen, todo = [], list(deps[m])
    while todo:
        d = todo.pop()
        if d not in seen and d != m:
            seen.append(d)
            todo += deps[d]
    return seen


# --- route structure ---------------------------------------------------------------------------------------------------
def load_equivalences() -> list:
    data = json.loads((pipeline.RULES_DIR / 'route-equivalences.json').read_text(encoding='utf-8'))
    return [(re.compile(e['pattern']), e['replace']) for e in data['equivalences']]


def read_dump(path: Path, equivalences: list) -> tuple[dict, list]:
    routes, errors, key = {}, [], None
    for line in path.read_text(encoding='utf-8').splitlines():
        for rx, repl in equivalences:
            line = rx.sub(repl, line)
        if line.startswith('ROUTE '):
            key = line[6:]
            routes[key] = []
        elif line.startswith('ERROR '):
            errors.append(line[6:])
        elif key:
            routes[key].append(line)
    return routes, errors


def compare_dumps(old: dict, new: dict) -> dict:
    """old/new: route key -> lines (indentation = nesting, so a node that moved under another parent is a different line)."""
    same, different, only_old, only_new = [], [], [], [k for k in new if k not in old]
    for key, a in old.items():
        if key not in new:
            only_old.append(key)
            continue
        b = new[key]
        if a == b:
            same.append(key)
            continue
        ca, cb = Counter(a), Counter(b)
        if ca == cb:
            same.append(key)  # same nodes at the same depth: only the order of sibling definitions differs
            continue
        lines = [{'side': 'original', 'depth': (len(x) - len(x.lstrip())) // 2, 'node': x.strip()} for x in a if (ca - cb)[x] > 0] \
            + [{'side': 'migrado', 'depth': (len(x) - len(x.lstrip())) // 2, 'node': x.strip()} for x in b if (cb - ca)[x] > 0]
        different.append({'route': key, 'lines': lines})
    return {'same': same, 'different': different, 'onlyOriginal': only_old, 'onlyMigrated': only_new}


def routes(source: Path, migrated: Path, out: Path, config: Path | None = None, log=lambda m: None) -> dict:
    """Route-structure comparison. migrated: the built `migrated-workspace` (modules/*/target/classes and the WAR lib)."""
    source, migrated, out = Path(source).resolve(), Path(migrated).resolve(), Path(out).resolve()
    work = out / '.verify'
    projects = pipeline.discover(source)
    names = {pipeline._head(pipeline.read(p / 'pom.xml'), 'artifactId'): p for p in projects}
    war_lib = migrated / 'webapp' / 'target' / 'ROOT' / 'WEB-INF' / 'lib'
    if not war_lib.is_dir():
        raise ToolError(f'El workspace migrado no está compilado (falta {war_lib}): ejecute migrate --build')
    lib2 = baseline_libs(work, camel2_version(projects), log)
    tool = work / 'tool'
    logs = work / 'logs'
    if not _compile([VERIFY_DIR / 'RouteDump.java'], [], tool, '11', logs / 'tool.log'):
        raise ToolError(f"No compiló RouteDump.java; ver {logs / 'tool.log'}")

    src_dirs = {m: p / 'src' / 'main' / 'java' for m, p in names.items()}
    deps = module_dependencies(src_dirs)
    cfg = pipeline.load_config(config)
    patched = {}
    for p in cfg.get('patches', []):
        patched.setdefault(p['module'], {}).setdefault(Path(p['file']).stem, []).append(p['name'])

    # Camel 4 classpath: WAR libraries WITHOUT the migrated modules (some share class names; each is dumped on its own)
    module_jar = re.compile('^(' + '|'.join(re.escape(m) for m in names) + r'|migrated-war)-\d')
    lib4 = [j for j in sorted(war_lib.glob('*.jar')) if not module_jar.match(j.name)]
    servlet = sorted((Path(os.environ.get('MF_MAVEN_REPO_LOCAL') or Path.home() / '.m2' / 'repository') / 'jakarta/servlet/jakarta.servlet-api').rglob('*.jar'))
    if not servlet:
        raise ToolError('No se encontró jakarta.servlet-api en el repositorio Maven local (compile primero el workspace migrado)')
    lib4.append(servlet[-1])

    equivalences = load_equivalences()
    java = _java('java')
    modules, totals = [], Counter()
    for m in _order(deps):
        java_dir = src_dirs[m]
        files = sorted(java_dir.rglob('*.java')) if java_dir.is_dir() else []
        new_classes = migrated / 'modules' / m / 'target' / 'classes'
        if not files or not new_classes.is_dir():
            continue
        log(f'comparando rutas de {m}')
        chain = _closure(m, deps)
        old_classes = work / 'classes' / m
        entry = {'module': m, 'status': 'PASS'}
        if not _compile(files, [lib2 / '*'] + [work / 'classes' / d for d in chain], old_classes, '8', logs / f'compile-{m}.log'):
            entry |= {'status': 'NOT_EXECUTABLE', 'detail': f"el original no compila contra la línea base; ver {logs / f'compile-{m}.log'}"}
            modules.append(entry)
            totals['notExecutable'] += 1
            continue
        dumps = {}
        for side, classes, cp in (('camel2', old_classes, [tool, lib2 / '*'] + [work / 'classes' / d for d in chain]),
                                  ('camel4', new_classes, [tool] + lib4 + [migrated / 'modules' / d / 'target' / 'classes' for d in chain])):
            dump = work / 'dump' / side / f'{m}.txt'
            dump.parent.mkdir(parents=True, exist_ok=True)
            dump.unlink(missing_ok=True)
            _run([java, 'RouteDump', classes, dump], logs / f'dump-{side}-{m}.log', classpath=cp)
            if not dump.exists():
                entry |= {'status': 'NOT_EXECUTABLE', 'detail': f"no se pudo volcar ({side}); ver {logs / f'dump-{side}-{m}.log'}"}
                break
            dumps[side] = read_dump(dump, equivalences)
        if entry['status'] == 'NOT_EXECUTABLE':
            modules.append(entry)
            totals['notExecutable'] += 1
            continue
        res = compare_dumps(dumps['camel2'][0], dumps['camel4'][0])
        by_class = patched.get(m, {})

        def reviewed(key):  # the difference is in a class the client configuration patches on purpose
            return by_class.get(key.split(' | ', 1)[0])

        unexpected = [d for d in res['different'] if not reviewed(d['route'])] \
            + [{'route': k, 'lines': [{'side': s, 'depth': 0, 'node': '(ruta completa)'}]} for s, ks in (('original', res['onlyOriginal']), ('migrado', res['onlyMigrated']))
               for k in ks if not reviewed(k)]
        expected = [{'route': d['route'], 'patches': reviewed(d['route'])} for d in res['different'] if reviewed(d['route'])] \
            + [{'route': k, 'patches': reviewed(k)} for k in res['onlyOriginal'] + res['onlyMigrated'] if reviewed(k)]
        not_dumped = [f'original: {e}' for e in dumps['camel2'][1]] + [f'migrado: {e}' for e in dumps['camel4'][1]]
        entry |= {'identical': len(res['same']), 'unexpected': unexpected, 'expectedByPatch': expected, 'notDumped': not_dumped,
                  'status': 'FAIL' if unexpected else 'REVIEW' if not_dumped else 'PASS'}
        totals['identical'] += len(res['same'])
        totals['unexpected'] += len(unexpected)
        totals['expectedByPatch'] += len(expected)
        totals['notDumped'] += len(not_dumped)
        modules.append(entry)
    status = 'FAIL' if totals['unexpected'] else 'REVIEW' if totals['notDumped'] or totals['notExecutable'] else 'PASS'
    result = {'check': 'route-structure', 'status': status, 'totals': {k: totals[k] for k in ('identical', 'unexpected', 'expectedByPatch', 'notDumped', 'notExecutable')},
              'modules': modules,
              'limits': ['Solo rutas definidas en RouteBuilder Java; las rutas en XML no se comparan.',
                         'Compara cómo queda armada la ruta, no lo que pasa al ejecutarla (ver la comprobación de respuestas).']}
    _save(out, 'route-structure', result, routes_markdown(result))
    return result


def routes_markdown(r: dict) -> str:
    t = r['totals']
    out = ['# Estructura de las rutas: original contra migrado', '', f"Estado: **{r['status']}**", '',
           '| Medida | Rutas |', '|---|---|', f"| Idénticas | {t['identical']} |", f"| Distintas por un parche documentado | {t['expectedByPatch']} |",
           f"| Distintas sin explicación | {t['unexpected']} |", f"| Clases que no se pudieron volcar | {t['notDumped']} |",
           f"| Módulos no ejecutables | {t['notExecutable']} |", '']
    for m in r['modules']:
        if m.get('unexpected'):
            out += [f"## {m['module']}: diferencias sin explicación", '']
            for d in m['unexpected']:
                out += [f"- `{d['route']}`"] + [f"  - {x['side']} (nivel {x['depth']}): `{x['node'][:200]}`" for x in d['lines']]
            out.append('')
    for m in r['modules']:
        if m.get('expectedByPatch'):
            out += [f"## {m['module']}: diferencias por parche", ''] + [f"- `{e['route']}` — {'; '.join(e['patches'])}" for e in m['expectedByPatch']] + ['']
        if m.get('notDumped') or m.get('detail'):
            out += [f"## {m['module']}: sin comparar", ''] + [f'- {x}' for x in m.get('notDumped', []) + ([m['detail']] if m.get('detail') else [])] + ['']
    return '\n'.join(out + ['## Límites', ''] + [f'- {x}' for x in r['limits']]) + '\n'


# --- baseline server ---------------------------------------------------------------------------------------------------
def baseline_command(source: Path, out: Path, modules: list, servlets: str, etc: Path, port: int = 18081, extra_classpath: list = (),
                     log=lambda m: None) -> tuple[list, dict]:
    """Prepare the baseline (original code + Camel 2 + Jetty) and return (java command, environment) to run it in the foreground."""
    source, out = Path(source).resolve(), Path(out).resolve()
    work = out / '.verify'
    projects = pipeline.discover(source)
    names = {pipeline._head(pipeline.read(p / 'pom.xml'), 'artifactId'): p for p in projects}
    unknown = [m for m in modules if m not in names]
    if unknown:
        raise ToolError(f"Módulos desconocidos: {', '.join(unknown)}")
    lib2 = baseline_libs(work, camel2_version(projects), log)
    compat = pipeline.load_rules()['compatPackage']
    deps = module_dependencies({m: p / 'src' / 'main' / 'java' for m, p in names.items()})
    logs = work / 'logs'
    tool = work / 'baseline-tool'
    osgi = sorted((pipeline.RULES_DIR / 'template/compat/src/main/java' / compat.replace('.', '/') / 'osgi').glob('*.java'))
    if not _compile([VERIFY_DIR / 'BaselineServer.java'] + osgi, [lib2 / '*'], tool, '8', logs / 'baseline-tool.log'):
        raise ToolError(f"No compiló el servidor de línea base; ver {logs / 'baseline-tool.log'}")
    classpath, xml_dirs = [tool], []
    wanted = [m for m in _order(deps) if m in modules or any(m in _closure(x, deps) for x in modules)]
    for m in wanted:
        src = names[m] / 'src' / 'main'
        files = sorted((src / 'java').rglob('*.java')) if (src / 'java').is_dir() else []
        if files:
            if not _compile(files, [lib2 / '*'] + [work / 'classes' / d for d in _closure(m, deps)], work / 'classes' / m, '8', logs / f'compile-{m}.log'):
                raise ToolError(f"{m} no compila contra la línea base; ver {logs / f'compile-{m}.log'}")
            classpath.append(work / 'classes' / m)
        if (src / 'resources').is_dir():
            classpath.append(src / 'resources')
        if m in modules:
            xdir = work / 'baseline-xml' / m
            shutil.rmtree(xdir, ignore_errors=True)
            for f in sorted((src / 'resources' / 'OSGI-INF' / 'blueprint').glob('*.xml')):
                text = pipeline.read(f)
                if not blueprint.is_servlet_registration(text):  # servlets are registered by the baseline server itself
                    pipeline.write(xdir / f.name, blueprint.convert(text, f.name, [], compat, camel2=True))
            if xdir.is_dir():
                xml_dirs.append(xdir)
    classpath += [lib2 / '*'] + [Path(c) for c in extra_classpath]
    cmd = [_java('java')] + [f'--add-opens={o}=ALL-UNNAMED' for o in OPENS] + [f'-Dmf.etc={Path(etc).resolve()}', 'BaselineServer', str(port), servlets]
    return cmd + [str(x) for x in xml_dirs], _env(classpath)


# --- responses ---------------------------------------------------------------------------------------------------------
NORMALIZE = [(re.compile(r'Exchange\[[^\]]*\]'), 'Exchange[ID]')]


def _send(base: str, case: dict, timeout: int, hosts: tuple = ()) -> dict:
    """http.client on purpose: urllib rewrites header names (memberNumber -> Membernumber) and header-name case is exactly
    one of the things that differ between servlet containers."""
    import http.client
    from urllib.parse import urlsplit
    u = urlsplit(base)
    body = case.get('body')
    headers = dict(case.get('headers') or {})
    if body is not None:
        headers.setdefault('Content-Type', case.get('contentType', 'text/xml; charset=UTF-8'))
    conn = (http.client.HTTPSConnection if u.scheme == 'https' else http.client.HTTPConnection)(u.hostname, u.port, timeout=timeout)
    try:
        conn.request(case.get('method', 'GET'), u.path.rstrip('/') + case['path'], body=body.encode('utf-8') if body is not None else None, headers=headers)
        r = conn.getresponse()
        status, ctype, raw = r.status, r.getheader('Content-Type') or '', r.read()
    except (OSError, http.client.HTTPException) as e:
        return {'status': 0, 'type': '', 'body': f'SIN RESPUESTA: {e}'}
    finally:
        conn.close()
    text = raw.decode('utf-8', errors='replace')
    for rx, repl in NORMALIZE:
        text = rx.sub(repl, text)
    for h in hosts:  # both systems may quote either address (e.g. a shared backend URL in an error message)
        text = text.replace(h, 'HOST')
    if re.search(r'^\s+at [\w.$]+\(', text, re.M):  # stack trace: its frames depend on library versions; compare type and message
        text = 'TRAZA: ' + text.splitlines()[0]
    return {'status': status, 'type': re.sub(r'\s', '', ctype).lower(), 'body': text}


def responses(old: str, new: str, cases_file: Path, out: Path, timeout: int = 60, log=lambda m: None) -> dict:
    """cases_file: {"cases": [{"name", "method", "path", "headers", "body", "contentType", "accept": "motivo"}]}.
    `accept` records that a reviewer accepted a known difference for that case (with the reason); it never hides it."""
    data = json.loads(Path(cases_file).read_text(encoding='utf-8'))
    cases = data['cases'] if isinstance(data, dict) else data
    if not cases:
        raise ToolError('El archivo de casos está vacío')
    results, totals = [], Counter()
    hosts = tuple(sorted({x.split('//', 1)[-1].rstrip('/') for x in (old, new)}, key=len, reverse=True))
    for c in cases:
        if not c.get('name') or not c.get('path'):
            raise ToolError(f'Caso sin name o path: {c}')
        a, b = _send(old, c, timeout, hosts), _send(new, c, timeout, hosts)
        if a['status'] == 0 or b['status'] == 0:
            state, issues = 'NOT_EXECUTABLE', [x['body'] for x in (a, b) if x['status'] == 0]
        else:
            issues = ([f"HTTP {a['status']} -> {b['status']}"] if a['status'] != b['status'] else []) \
                + ([f"Content-Type '{a['type']}' -> '{b['type']}'"] if a['type'] != b['type'] else []) \
                + (['cuerpo distinto'] if a['body'] != b['body'] else [])
            state = 'SAME' if not issues else 'ACCEPTED' if c.get('accept') else 'DIFFERENT'
        totals[state] += 1
        log(f"{state:<14} {c['name']}" + (f" ({'; '.join(issues)})" if issues and state != 'NOT_EXECUTABLE' else ''))
        results.append({'name': c['name'], 'method': c.get('method', 'GET'), 'path': c['path'], 'state': state, 'issues': issues,
                        'accept': c.get('accept'), 'original': a if issues else {'status': a['status']}, 'migrated': b if issues else {'status': b['status']}})
    status = ('NOT_EXECUTABLE' if totals['NOT_EXECUTABLE'] == len(cases) else 'FAIL' if totals['DIFFERENT'] or totals['NOT_EXECUTABLE']
              else 'PASS_WITH_ACCEPTED' if totals['ACCEPTED'] else 'PASS')
    result = {'check': 'responses', 'status': status, 'original': old, 'migrated': new,
              'totals': {'cases': len(cases), 'same': totals['SAME'], 'different': totals['DIFFERENT'], 'accepted': totals['ACCEPTED'],
                         'notExecutable': totals['NOT_EXECUTABLE']}, 'cases': results}
    _save(out, 'responses', result, responses_markdown(result))
    return result


def responses_markdown(r: dict) -> str:
    t = r['totals']
    out = ['# Respuestas: original contra migrado', '', f"Estado: **{r['status']}** — {t['cases']} casos: {t['same']} idénticos, {t['different']} distintos, "
           f"{t['accepted']} con diferencia aceptada, {t['notExecutable']} sin ejecutar.", '', f"Original: `{r['original']}` · Migrado: `{r['migrated']}`", '',
           '| Caso | Resultado | Diferencia |', '|---|---|---|']
    out += [f"| {c['name']} | {c['state']} | {'; '.join(c['issues']) or '—'}{' — aceptada: ' + c['accept'] if c['state'] == 'ACCEPTED' else ''} |" for c in r['cases']]
    diff = [c for c in r['cases'] if c['state'] in ('DIFFERENT', 'ACCEPTED')]
    if diff:
        out += ['', '## Detalle de las diferencias', '']
        for c in diff:
            out += [f"### {c['name']}", '', f"`{c['method']} {c['path']}`", '', '```text',
                    f"ORIGINAL: HTTP {c['original']['status']} [{c['original']['type']}] {re.sub(chr(92) + 's+', ' ', c['original']['body'])[:1200]}",
                    f"MIGRADO:  HTTP {c['migrated']['status']} [{c['migrated']['type']}] {re.sub(chr(92) + 's+', ' ', c['migrated']['body'])[:1200]}", '```', '']
    return '\n'.join(out) + '\n'


# --- evidence ----------------------------------------------------------------------------------------------------------
def _save(out: Path, name: str, result: dict, markdown: str):
    reports = out / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f'{name}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    (reports / f'{name}.md').write_text(markdown, encoding='utf-8')
    evidence = {}
    for check in ('route-structure', 'responses'):
        f = reports / f'{check}.json'
        if f.exists():
            d = json.loads(f.read_text(encoding='utf-8'))
            evidence[check] = {'status': d['status'], 'totals': d['totals'], 'report': f'reports/{check}.md'}
    states = [e['status'] for e in evidence.values()]
    overall = ('FAIL' if 'FAIL' in states else 'REVIEW' if any(s in ('REVIEW', 'NOT_EXECUTABLE') for s in states) or len(states) < 2
               else 'PASS_WITH_ACCEPTED' if 'PASS_WITH_ACCEPTED' in states else 'PASS')
    (reports / 'equivalence-evidence.json').write_text(json.dumps({
        'gate': 'G4', 'status': overall, 'checks': evidence,
        'missing': [c for c in ('route-structure', 'responses') if c not in evidence],
        'note': 'Evidencia para registrar en el gate G4. PASS exige las dos comprobaciones; no sustituye las pruebas con los sistemas reales del cliente.',
    }, ensure_ascii=False, indent=2), encoding='utf-8')

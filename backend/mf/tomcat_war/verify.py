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


# Camel components Karaf installs as features (not declared in the bundles' poms) and the code reaches by URI scheme
KARAF_FEATURE_COMPONENTS = {'exec': 'camel-exec', 'ssh': 'camel-ssh', 'scp': 'camel-jsch', 'stream': 'camel-stream',
                            'velocity': 'camel-velocity', 'freemarker': 'camel-freemarker', 'zipfile': 'camel-zipfile',
                            'kafka': 'camel-kafka', 'mongodb': 'camel-mongodb', 'jetty': 'camel-jetty', 'netty4': 'camel-netty4',
                            'smpp': 'camel-smpp', 'ldap': 'camel-ldap', 'jdbc': 'camel-jdbc', 'xpath': 'camel-saxon'}


def feature_components(projects: list[Path]) -> list:
    """Artifacts of KARAF_FEATURE_COMPONENTS the original code uses ("exec:...", 'scp://...')."""
    found = set()
    pattern = re.compile(r'["\'](' + '|'.join(KARAF_FEATURE_COMPONENTS) + r'):', re.I)
    for p in projects:
        for f in (p / 'src' / 'main').rglob('*') if (p / 'src' / 'main').is_dir() else []:
            if f.suffix in ('.java', '.xml') and f.is_file():
                found |= {KARAF_FEATURE_COMPONENTS[m.lower()] for m in pattern.findall(pipeline.read(f))}
    return sorted(found)


def baseline_libs(work: Path, version: str, log=lambda m: None, extra: list = ()) -> Path:
    """Camel 2 libraries the original bundles compile against (resolved once per work directory). extra: further Camel 2
    artifactIds (feature_components)."""
    lib = work / 'baseline' / 'lib'
    marker = work / 'baseline' / f".resolved-{version}{'-' + '-'.join(extra) if extra else ''}"
    if marker.exists() and any(lib.glob('*.jar')):
        return lib
    mvn = shutil.which('mvn')
    if not mvn:
        raise ToolError('mvn no está en el PATH')
    pom = work / 'baseline' / 'pom.xml'
    pom.parent.mkdir(parents=True, exist_ok=True)
    deps = ''.join(f'\t\t<dependency><groupId>org.apache.camel</groupId><artifactId>{a}</artifactId><version>${{camel.version}}</version></dependency>\n'
                   for a in extra)
    pom.write_text((VERIFY_DIR / 'baseline-pom.xml').read_text(encoding='utf-8').replace('@CAMEL2_VERSION@', version)
                   .replace('\t</dependencies>', deps + '\t</dependencies>', 1), encoding='utf-8')
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


_XML_ROUTE = re.compile(r'<route\b(?:[^>"]|"[^"]*")*?(?:/>|>.*?</route\s*>)', re.S)


def xml_routes(text: str) -> tuple[str | None, list]:
    """The <route> elements of a Blueprint/Spring XML as one <routes> document that either Camel version can load on its
    own (namespace prefixes declared in the file are kept). Returns (document or None, notes)."""
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    notes = [f'rutas con prefijo de namespace ({p}:route) sin comparar' for p in sorted(set(re.findall(r'<([\w-]+):route\b', text)))]
    found = [re.sub(r'\sxmlns="http://camel\.apache\.org/schema/(?:blueprint|spring)"', '', r) for r in _XML_ROUTE.findall(text)]
    if not found:
        return None, notes
    prefixes = dict(re.findall(r'xmlns:([\w-]+)\s*=\s*"([^"]+)"', text))
    decl = ''.join(f' xmlns:{k}="{v}"' for k, v in sorted(prefixes.items()))
    return '<routes xmlns="http://camel.apache.org/schema/spring"' + decl + '>\n' + '\n'.join(found) + '\n</routes>\n', notes


def _xml_dir(folder: Path, target: Path) -> tuple[int, list]:
    """Writes the routes of each XML in folder to target (same file names). Returns (files with routes, notes)."""
    shutil.rmtree(target, ignore_errors=True)
    n, notes = 0, []
    for f in sorted(folder.glob('*.xml')) if folder.is_dir() else []:
        doc, extra = xml_routes(pipeline.read(f))
        notes += [f'{f.name}: {x}' for x in extra]
        if doc:
            pipeline.write(target / f.name, doc)
            n += 1
    return n, notes


_END = re.compile(r'\.end(?:Choice)?\(\)')


def fix_route(java_file: Path, cls: str, from_uri: str, expected: dict, trial: Path, compile_cp: list, dump_cp: list,
              equivalences: list, logs: Path, base_classes: Path, release: str = '17') -> dict | None:
    """Camel 2 and Camel 4 close blocks differently on some `.end()`/`.endChoice()` (e.g. `.end()` after a filter inside a
    choice branch returns to the branch in Camel 2 and to the choice in Camel 4): the same source builds another tree without
    any error. Tries removing each `.end()`/`.endChoice()` of the route, one at a time, recompiles that class and re-dumps it;
    a removal is accepted only when EVERY route of the class then has exactly the Camel 2 structure. Returns the edit or None.
    ponytail: single removals only (the cases seen on real code); adding an end() or two edits at once is not tried."""
    text = pipeline.read(java_file)
    m = re.search(r'\bfrom\(\s*"' + re.escape(from_uri) + r'"', text)
    if not m:
        return None
    end = re.compile(r'\)\s*;').search(text, m.end())
    if not end:
        return None
    for i, tok in enumerate(_END.finditer(text, m.start(), end.end())):
        candidate = text[:tok.start()] + text[tok.end():]
        if trial.exists():
            shutil.rmtree(trial)
        shutil.copytree(base_classes, trial)
        src = trial.parent / f'{trial.name}-src' / java_file.name
        pipeline.write(src, candidate)
        if not _compile([src], compile_cp + [trial], trial, release, logs / f'fix-{cls}-{i}.log', encoding='ISO-8859-1'):
            continue
        dump = trial.parent / f'{trial.name}.txt'
        dump.unlink(missing_ok=True)
        _run([_java('java'), 'RouteDump', trial, dump], logs / f'fix-dump-{cls}-{i}.log', classpath=dump_cp)
        if not dump.exists():
            continue
        got = {k: v for k, v in read_dump(dump, equivalences)[0].items() if k.split(' | ', 1)[0] == cls}
        if got and got.keys() == expected.keys() and all(Counter(got[k]) == Counter(expected[k]) for k in got):
            return {'text': candidate, 'line': text.count('\n', 0, tok.start()) + 1, 'removed': tok.group(0)}
    return None


def dump_migrated(source: Path, module_dirs: dict, war_dir: Path, work: Path, log=lambda m: None) -> dict:
    """Routes (Camel 4) of migrated modules taken straight from an exploded WAR: module_dirs = {artifactId: folder with the
    module jar unpacked}. Returns {artifactId: {route key: lines}}; a module that cannot be dumped is left out."""
    tool, logs = work / 'tool', work / 'logs'
    if not _compile([VERIFY_DIR / 'RouteDump.java'], [], tool, '11', logs / 'tool.log'):
        raise ToolError(f"No compiló RouteDump.java; ver {logs / 'tool.log'}")
    lib = war_dir / 'WEB-INF' / 'lib'
    module_jar = re.compile('^(' + '|'.join(re.escape(m) for m in module_dirs) + r')-\d')
    lib4 = [j for j in sorted(lib.glob('*.jar')) if not module_jar.match(j.name)]
    servlet = sorted((Path(os.environ.get('MF_MAVEN_REPO_LOCAL') or Path.home() / '.m2' / 'repository') / 'jakarta/servlet/jakarta.servlet-api').rglob('*.jar'))
    lib4 += servlet[-1:]
    projects = {pipeline._head(pipeline.read(p / 'pom.xml'), 'artifactId'): p for p in pipeline.discover(Path(source))}
    deps = module_dependencies({m: p / 'src' / 'main' / 'java' for m, p in projects.items()})
    out, equivalences = {}, load_equivalences()
    for m, classes in sorted(module_dirs.items()):
        chain = [module_dirs[d] for d in _closure(m, deps) if d in module_dirs] if m in deps else []
        dump = work / 'dump-auto' / f'{m}.txt'
        dump.parent.mkdir(parents=True, exist_ok=True)
        dump.unlink(missing_ok=True)
        _run([_java('java'), 'RouteDump', classes, dump], logs / f'dump-auto-{m}.log', classpath=[tool] + lib4 + chain)
        if dump.exists():
            out[m] = read_dump(dump, equivalences)[0]
        else:
            log(f'{m}: no se pudieron leer sus rutas (ver dump-auto-{m}.log)')
    return out


def routes(source: Path, migrated: Path, out: Path, config: Path | None = None, log=lambda m: None, fix: bool = False) -> dict:
    """Route-structure comparison. migrated: the built `migrated-workspace` (modules/*/target/classes and the WAR lib).
    fix=True: unexplained differences that a single `.end()` removal makes identical to Camel 2 are corrected in the migrated
    sources (and target/classes); the caller rebuilds the WAR. Listed as `autoCorrected` for review."""
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
        # routes defined in XML: the original Blueprint files against the converted ones, loaded by each Camel version
        xml_old, notes_old = _xml_dir(names[m] / 'src' / 'main' / 'resources' / 'OSGI-INF' / 'blueprint', work / 'xml' / 'camel2' / m)
        xml_new, notes_new = _xml_dir(migrated / 'modules' / m / 'src' / 'main' / 'resources' / 'META-INF' / 'modules' / m, work / 'xml' / 'camel4' / m)
        if not (files or xml_old or xml_new) or not new_classes.is_dir():
            continue
        log(f'comparando rutas de {m}')
        chain = _closure(m, deps)
        old_classes = work / 'classes' / m
        old_classes.mkdir(parents=True, exist_ok=True)
        entry = {'module': m, 'status': 'PASS'}
        if files and not _compile(files, [lib2 / '*'] + [work / 'classes' / d for d in chain], old_classes, '8', logs / f'compile-{m}.log'):
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
            # noOptimize: the JAXB 2.2 of Camel 2 generates accessor classes through a JDK API removed after Java 8
            _run([java, '-Dcom.sun.xml.bind.v2.bytecode.ClassTailor.noOptimize=true', 'RouteDump', classes, dump, work / 'xml' / side / m],
                 logs / f'dump-{side}-{m}.log', classpath=cp)
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
        corrected = []
        if fix and unexpected:
            release = (re.search(r'<maven.compiler.release>(\d+)<', pipeline.read(migrated / 'pom.xml')) or [None, '17'])[1]
            src_root = migrated / 'modules' / m / 'src' / 'main' / 'java'
            for cls in sorted({u['route'].split(' | ', 1)[0] for u in unexpected}):
                files = sorted(src_root.rglob(f'{cls}.java')) if src_root.is_dir() else []
                class_routes = [u['route'] for u in unexpected if u['route'].split(' | ', 1)[0] == cls]
                if len(files) != 1 or any(' | ' not in r for r in class_routes):
                    continue
                want = {k: v for k, v in dumps['camel2'][0].items() if k.split(' | ', 1)[0] == cls}
                done = None
                for r in class_routes:  # the from() whose statement holds the end() to remove
                    done = fix_route(files[0], cls, r.split(' | ', 1)[1], want, work / 'fix' / m,
                                     lib4 + [migrated / 'modules' / d / 'target' / 'classes' for d in chain],
                                     [tool] + lib4 + [migrated / 'modules' / d / 'target' / 'classes' for d in chain],
                                     equivalences, logs, new_classes, release)
                    if done:
                        break
                if not done:
                    continue
                pipeline.write(files[0], done['text'])
                shutil.copytree(work / 'fix' / m, new_classes, dirs_exist_ok=True)  # dependants and the rebuild see the fix
                rel = files[0].relative_to(migrated).as_posix()
                log(f"corregido {rel}:{done['line']} (se quitó {done['removed']})")
                corrected += [{'route': r, 'file': rel, 'line': done['line'], 'removed': done['removed'],
                               'detail': 'Camel 2 armaba esta ruta con otra anidación; se conserva la de Camel 2 (lo que corre hoy). '
                                         'Revisar si el original era intencional.'} for r in class_routes]
            unexpected = [u for u in unexpected if u['route'] not in {c['route'] for c in corrected}]
        not_dumped = [f'original: {e}' for e in dumps['camel2'][1]] + [f'migrado: {e}' for e in dumps['camel4'][1]]
        not_dumped += [f'original: {e}' for e in notes_old] + [f'migrado: {e}' for e in notes_new]
        entry |= {'identical': len(res['same']), 'unexpected': unexpected, 'expectedByPatch': expected, 'notDumped': not_dumped,
                  'autoCorrected': corrected, 'status': 'FAIL' if unexpected else 'REVIEW' if not_dumped or corrected else 'PASS'}
        totals['autoCorrected'] += len(corrected)
        totals['identical'] += len(res['same'])
        totals['unexpected'] += len(unexpected)
        totals['expectedByPatch'] += len(expected)
        totals['notDumped'] += len(not_dumped)
        modules.append(entry)
    status = 'FAIL' if totals['unexpected'] else 'REVIEW' if totals['notDumped'] or totals['notExecutable'] or totals['autoCorrected'] else 'PASS'
    result = {'check': 'route-structure', 'status': status,
              'totals': {k: totals[k] for k in ('identical', 'unexpected', 'expectedByPatch', 'autoCorrected', 'notDumped', 'notExecutable')},
              'modules': modules,
              'limits': ['Rutas de RouteBuilder Java y elementos <route> de los XML Blueprint/Spring; no se comparan <rest> en XML ni rutas en otros DSL.',
                         'Compara cómo queda armada la ruta, no lo que pasa al ejecutarla (ver la comprobación de respuestas).']}
    _save(out, 'route-structure', result, routes_markdown(result))
    return result


def routes_markdown(r: dict) -> str:
    t = r['totals']
    out = ['# Estructura de las rutas: original contra migrado', '', f"Estado: **{r['status']}**", '',
           '| Medida | Rutas |', '|---|---|', f"| Idénticas | {t['identical']} |", f"| Distintas por un parche documentado | {t['expectedByPatch']} |",
           f"| Distintas sin explicación | {t['unexpected']} |", f"| Corregidas automáticamente (revisar) | {t.get('autoCorrected', 0)} |",
           f"| Clases que no se pudieron volcar | {t['notDumped']} |",
           f"| Módulos no ejecutables | {t['notExecutable']} |", '']
    for m in r['modules']:
        if m.get('unexpected'):
            out += [f"## {m['module']}: diferencias sin explicación", '']
            for d in m['unexpected']:
                out += [f"- `{d['route']}`"] + [f"  - {x['side']} (nivel {x['depth']}): `{x['node'][:200]}`" for x in d['lines']]
            out.append('')
    for m in r['modules']:
        if m.get('autoCorrected'):
            out += [f"## {m['module']}: corregidas automáticamente", '']
            out += [f"- `{c['route']}`: se quitó `{c['removed']}` en `{c['file']}:{c['line']}`. {c['detail']}" for c in m['autoCorrected']] + ['']
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
    lib2 = baseline_libs(work, camel2_version(projects), log, feature_components([names[m] for m in modules]))
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
    # In Karaf each library was its own bundle; on one classpath, jars that embed an older copy of another library's classes
    # shadow it (ehcache 2.x carries an old terracotta-statistics that ehcache 3.x cannot use). Newer major versions first.
    jars = sorted(lib2.glob('*.jar'), key=lambda j: (not re.match(r'ehcache-3', j.name), j.name))
    classpath += jars + [Path(c) for c in extra_classpath]
    cmd = [_java('java')] + [f'--add-opens={o}=ALL-UNNAMED' for o in OPENS] + [f'-Dmf.etc={Path(etc).resolve()}', 'BaselineServer', str(port), servlets]
    return cmd + [str(x) for x in xml_dirs], _env(classpath)


# --- responses ---------------------------------------------------------------------------------------------------------
# Differences in how each version PRINTS the same thing (never in what it does): exchange ids; Java identity hashes
# (Schema@2a925cac); a header predicate, which Camel 2 prints `header{header(x)}` and Camel 4 `header(x)`; the "Simple: "
# Camel 2 puts before a simple predicate; the "; nested exception is ..." Spring 4/5 appended to an exception message and
# Spring 6 no longer does (a stack trace is already reduced to its first line).
NORMALIZE = [(re.compile(r'Exchange\[[^\]]*\]'), 'Exchange[ID]'),
             (re.compile(r'\b([A-Z][\w$]*)@[0-9a-f]{4,16}\b'), r'\1@ID'),
             (re.compile(r'header\{header\(([^)]*)\)\}'), r'header(\1)'),
             (re.compile(r'header\{([\w.\-]+)\}'), r'header(\1)'),
             (re.compile(r'constant\{([^}]*)\}'), r'\1'),
             (re.compile(r'Predicate\[Simple: '), 'Predicate['),
             # same exception, moved to another package in Camel 3/4
             (re.compile(r'org\.apache\.camel\.processor\.validation\.(PredicateValidationException)'), r'org.apache.camel.support.processor.\1'),
             (re.compile(r'org\.apache\.camel\.http\.common\.(HttpOperationFailedException)'), r'org.apache.camel.http.base.\1'),
             (re.compile(r'; nested exception is [\w.$]+(?:Exception|Error)\b[^\n]*'), '')]


def _canonical(body: str) -> str:
    """The body as it is compared: JSON with its keys sorted and XML in canonical form (C14N 2.0: the order of
    attributes and namespace declarations does not count), so two serializations of the same document are equal.
    Anything else, as it came."""
    text = body.strip()
    if text[:1] in ('{', '['):
        try:
            return json.dumps(json.loads(text), sort_keys=True, ensure_ascii=False)
        except ValueError:
            return body
    if text.startswith('<') and not text[:15].lower().startswith(('<!doctype', '<html')):
        try:
            from xml.etree.ElementTree import canonicalize
            return canonicalize(text, strip_text=True)
        except Exception:  # not well formed: compare as text
            return body
    return body


def _same_type(a: dict, b: dict) -> bool:
    """Same Content-Type, or one that differs only in the charset parameter when both bodies are plain ASCII: the bytes on
    the wire are then identical (Jetty defaults text/* to ISO-8859-1, Tomcat to UTF-8)."""
    if a['type'] == b['type']:
        return True
    strip = lambda t: re.sub(r';?charset=[^;]*', '', t)
    return strip(a['type']) == strip(b['type']) and a['body'].isascii() and b['body'].isascii()


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


def compare_pair(a: dict, b: dict, accepted: bool = False) -> tuple[str, list, str | None]:
    """(state, issues, kind) of two answers from _send. kind never changes the state: ERROR_TEXT when both fail with the
    same HTTP code and only the text of the error (or its charset) differs; DATA for anything else."""
    if a['status'] == 0 or b['status'] == 0:
        return 'NOT_EXECUTABLE', [x['body'] for x in (a, b) if x['status'] == 0], None
    issues = ([f"HTTP {a['status']} -> {b['status']}"] if a['status'] != b['status'] else []) \
        + ([f"Content-Type '{a['type']}' -> '{b['type']}'"] if not _same_type(a, b) else []) \
        + (['cuerpo distinto'] if _canonical(a['body']) != _canonical(b['body']) else [])
    if not issues:
        return 'SAME', [], None
    return 'ACCEPTED' if accepted else 'DIFFERENT', issues, 'ERROR_TEXT' if a['status'] == b['status'] >= 400 else 'DATA'


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
        state, issues, kind = compare_pair(a, b, bool(c.get('accept')))
        totals[state] += 1
        log(f"{state:<14} {c['name']}" + (f" ({'; '.join(issues)})" if issues and state != 'NOT_EXECUTABLE' else ''))
        sent = {k: ('***' if k.lower() in ('authorization', 'cookie') else v) for k, v in (c.get('headers') or {}).items()}
        results.append({'name': c['name'], 'method': c.get('method', 'GET'), 'path': c['path'], 'state': state, 'kind': kind, 'issues': issues,
                        'accept': c.get('accept'), 'request': {'headers': sent, 'body': c.get('body')}, 'original': a, 'migrated': b})
    status = ('NOT_EXECUTABLE' if totals['NOT_EXECUTABLE'] == len(cases) else 'FAIL' if totals['DIFFERENT'] or totals['NOT_EXECUTABLE']
              else 'PASS_WITH_ACCEPTED' if totals['ACCEPTED'] else 'PASS')
    result = {'check': 'responses', 'status': status, 'original': old, 'migrated': new,
              'totals': {'cases': len(cases), 'same': totals['SAME'], 'different': totals['DIFFERENT'], 'accepted': totals['ACCEPTED'],
                         'notExecutable': totals['NOT_EXECUTABLE']},
              'differencesByKind': dict(Counter(r['kind'] for r in results if r['state'] == 'DIFFERENT')), 'cases': results}
    _save(out, 'responses', result, responses_markdown(result))
    return result


def responses_markdown(r: dict) -> str:
    t = r['totals']
    out = ['# Respuestas: original contra migrado', '', f"Estado: **{r['status']}** — {t['cases']} casos: {t['same']} idénticos, {t['different']} distintos, "
           f"{t['accepted']} con diferencia aceptada, {t['notExecutable']} sin ejecutar.", '', f"Original: `{r['original']}` · Migrado: `{r['migrated']}`", '',
           '| Caso | Resultado | Diferencia |', '|---|---|---|']
    out += [f"| {c['name']} | {c['state']} | {'; '.join(c['issues']) or '—'}{' (solo el texto del error)' if c.get('kind') == 'ERROR_TEXT' else ''}"
            f"{' — aceptada: ' + c['accept'] if c['state'] == 'ACCEPTED' else ''} |" for c in r['cases']]
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

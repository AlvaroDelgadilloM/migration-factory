"""Workspace migration for the `tomcat-war` profile.

Input: a folder with OSGi bundles (one Maven project per bundle, Camel 2 + Blueprint). Output: one multi-module Maven
project (compat + one jar per bundle + WAR) plus reports. The source is only read.

Client-specific decisions do not live in the engine: they come from a JSON file (`config`), see docs/19_TOMCAT_WAR.md:
  {"groupId": "...", "version": "...",
   "containers": {"names": ["a", "b"], "file": ".gitlab-ci.yml", "skipPattern": "SKIP_{NAME}:\\s*\"?true"},
   "patches": [{"name": "...", "module": "...", "file": "X.java", "find": "...", "replace": "...", "regex": false}],
   "extraWarDependencies": ["g:a:v"]}
A patch that no longer matches its file aborts the run: manual decisions are never applied blindly.
"""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from functools import lru_cache
from pathlib import Path

from . import blueprint

RULES_DIR = Path(__file__).resolve().parents[3] / 'rules' / 'tomcat-war'
BOMS_DIR = Path(__file__).resolve().parents[3] / 'rules' / 'camel' / 'boms'
LATIN1 = 'latin-1'  # every byte round-trips: replacements are ASCII and the original encoding is never altered
SKIP_DIRS = {'target', 'tags', '.git', '.svn', 'node_modules', '.idea'}
BLUEPRINT_FILE = re.compile(r'^resources/OSGI-INF/blueprint/(.+\.xml)$', re.I)
NL = '\r\n'


class InvalidInput(Exception):
    pass


class PatchMismatch(InvalidInput):
    pass


@lru_cache
def load_rules(path: str | None = None) -> dict:
    data = json.loads(Path(path or RULES_DIR / 'camel2-to-4.json').read_text(encoding='utf-8'))
    for r in data['javaRules'] + data['manualPatterns']:
        r['_re'] = re.compile(r['pattern'])
    for r in data['dependencyRules']:
        r['_re'] = re.compile(r['pattern'], re.I)
    data['_drop'] = re.compile(data['dropOriginalDependencies'], re.I)
    data['_ignored'] = re.compile(data['ignoredSourceFiles'], re.I)
    bom = data['profile']['camelBom']
    snap = BOMS_DIR / f"{bom.split(':')[1]}-{bom.split(':')[2]}.json"
    if not snap.exists():
        raise InvalidInput(f'Falta el snapshot verificado del BOM {bom} ({snap.name})')
    data['_bom'] = frozenset(json.loads(snap.read_text(encoding='utf-8'))['artifacts'])
    return data


def read(path: Path) -> str:
    return path.read_bytes().decode(LATIN1)


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(LATIN1))


def discover(source: Path) -> list[Path]:
    """Maven projects of the workspace (folders with pom.xml and src/main), in a stable order."""
    out = []
    for pom in sorted(source.rglob('pom.xml')):
        rel = pom.relative_to(source).parts
        if not SKIP_DIRS & set(rel[:-1]) and (pom.parent / 'src' / 'main').is_dir():
            out.append(pom.parent)
    return out


def _head(pom: str, tag: str) -> str:
    m = re.search(f'<{tag}>([^<]+)</{tag}>', re.split('<dependencies>', pom, flags=re.I)[0])
    return m.group(1) if m else ''


def apply_java_rules(text: str, rules: dict, compat: str, name: str, report: list) -> str:
    for r in rules['javaRules']:
        n = sum(1 for _ in r['_re'].finditer(text))
        if n:
            text = r['_re'].sub(r['replace'].replace('@COMPAT@', compat), text)
            report.append(f"  [java] {name} : {r['id']} x{n}")
    return text


def apply_patches(text: str, patches: list, module: str, name: str, report: list) -> tuple[str, int]:
    applied = 0
    for p in patches:
        if p['module'] != module or p['file'] != name:
            continue
        crlf = '\r\n' in text
        find, repl = p['find'].replace('\r\n', '\n'), p['replace'].replace('\r\n', '\n')
        norm = text.replace('\r\n', '\n')
        if p.get('regex'):
            if not re.search(find, norm):
                raise PatchMismatch(f"El parche '{p['name']}' no aplica en {module}/{name}: el texto original cambió")
            norm = re.sub(find, repl, norm)
        else:
            if find not in norm:
                raise PatchMismatch(f"El parche '{p['name']}' no aplica en {module}/{name}: el texto original cambió")
            norm = norm.replace(find, repl)
        text = norm.replace('\n', '\r\n') if crlf else norm
        report.append(f"  [parche] {name} : {p['name']}")
        applied += 1
    return text, applied


def nested_end_choice(text: str) -> int:
    """`.endChoice()` closing a nested choice: Camel 4 no longer returns to the outer choice, so a following
    when()/otherwise() lands on the wrong one WITHOUT any startup error. Heuristic count, for manual review."""
    nested = 0
    for stmt in re.finditer(r'\bfrom\(.*?;\s*\n', text, re.S):
        depth = 0
        for tok in re.finditer(r'\.(choice|endChoice|end)\(\)', stmt.group(0)):
            if tok.group(1) == 'choice':
                depth += 1
            elif tok.group(1) == 'endChoice':
                nested += depth >= 2
            elif depth > 0:
                depth -= 1
    return nested


def _dep_xml(g, a, v=None):
    return (f'\t\t<dependency>{NL}\t\t\t<groupId>{g}</groupId>{NL}\t\t\t<artifactId>{a}</artifactId>{NL}'
            + (f'\t\t\t<version>{v}</version>{NL}' if v else '') + f'\t\t</dependency>{NL}')


def module_pom(orig_pom: str, corpus: str, artifact_id: str, rules: dict, group_id: str, version: str) -> tuple[str, list]:
    deps = [d.replace('@GROUP_ID@', group_id) for d in rules['baseDependencies']]
    for r in rules['dependencyRules']:
        if r['_re'].search(corpus) and r['dependency'] not in deps:
            deps.append(r['dependency'])
    unknown = [d for d in deps if d.startswith('org.apache.camel:') and d not in rules['_bom']]
    if unknown:  # same principle as the compatibility registry: never write an artifact the target BOM does not manage
        raise InvalidInput(f"{artifact_id}: artefactos Camel fuera del BOM {rules['profile']['camelBom']}: {', '.join(unknown)}")
    xml = ''.join(_dep_xml(*d.split(':')) for d in deps)
    kept = []
    for m in re.finditer(r'<dependency>(.*?)</dependency>', orig_pom, re.S):  # the module's own libraries keep their version
        b = m.group(1)
        g, a, v = (re.search(f'<{t}>([^<]+)</{t}>', b) for t in ('groupId', 'artifactId', 'version'))
        g, a, v = (x.group(1) if x else '' for x in (g, a, v))
        if rules['_drop'].search(g) or re.search(r'<scope>(test|import)</scope>', b, re.I):
            continue
        prop = re.match(r'^\$\{([^}]+)\}$', v)
        if prop:  # version defined as a property of the original POM
            pm = re.search(f'<{re.escape(prop.group(1))}>([^<]+)<', orig_pom)
            v = pm.group(1) if pm else ''
        if not v:
            continue
        xml += _dep_xml(g, a, v)
        kept.append(f'{g}:{a}:{v}')
    pom = f'''<?xml version="1.0" encoding="UTF-8"?>
<!-- GENERADO por Migration Factory (perfil tomcat-war) a partir del pom.xml original (packaging bundle) -->
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
	xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/maven-v4_0_0.xsd">
	<modelVersion>4.0.0</modelVersion>
	<parent>
		<groupId>{group_id}</groupId>
		<artifactId>migrated-modules</artifactId>
		<version>{version}</version>
	</parent>

	<groupId>{_head(orig_pom, 'groupId')}</groupId>
	<artifactId>{artifact_id}</artifactId>
	<version>{_head(orig_pom, 'version')}</version>
	<name>{_head(orig_pom, 'name')}</name>
	<packaging>jar</packaging>

	<dependencies>
{xml}	</dependencies>
</project>
'''.replace('\r\n', '\n').replace('\n', NL)
    return pom, deps + kept


def _aggregator(group_id, version, parent, artifact, name, body):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!-- GENERADO por Migration Factory (perfil tomcat-war) -->
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
	xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/maven-v4_0_0.xsd">
	<modelVersion>4.0.0</modelVersion>
	<parent>
		<groupId>{group_id}</groupId>
		<artifactId>{parent}</artifactId>
		<version>{version}</version>
	</parent>
	<artifactId>{artifact}</artifactId>
	<packaging>pom</packaging>
	<name>{name}</name>

{body}
</project>
'''.replace('\r\n', '\n').replace('\n', NL)


def header_names(corpus: str, main: Path) -> list[str]:
    """Mixed-case header names the routes expect verbatim (Tomcat lower-cases HTTP header names; see HeaderCaseFilter):
    REST header parameters, header("...") and XSLT parameters."""
    names = set(re.findall(r'\.name\("([^"]+)"\)(?:(?!endParam)[\s\S]){0,200}?RestParamType\.header', corpus))
    names |= set(re.findall(r'\bheader\("([A-Za-z][\w-]*)"\)', corpus))
    for x in main.rglob('*'):
        if x.is_file() and x.suffix.lower() in ('.xsl', '.xslt'):
            names |= set(re.findall(r'<xsl:param\s+name="([^"]+)"', read(x)))
    return sorted(n for n in names if re.search('[A-Z]', n))


def migrate_module(src: Path, dst: Path, rules: dict, cfg: dict, compat: str, report: list, totals: dict) -> dict:
    orig_pom = read(src / 'pom.xml')
    artifact_id = _head(orig_pom, 'artifactId')
    module_dir = f'META-INF/modules/{artifact_id}'
    info = {'artifactId': artifact_id, 'groupId': _head(orig_pom, 'groupId'), 'version': _head(orig_pom, 'version'), 'xml': [],
            'javaFiles': 0, 'javaChanged': 0, 'manual': []}
    corpus = []
    main = src / 'src' / 'main'
    for f in sorted(p for p in main.rglob('*') if p.is_file()):
        rel = f.relative_to(main).as_posix()
        if rules['_ignored'].search(rel):
            continue
        bp = BLUEPRINT_FILE.match(rel)
        if bp:
            text = read(f)
            totals['xmlFiles'] += 1
            if blueprint.is_servlet_registration(text):
                frag, n = blueprint.servlet_fragment(text, bp.group(1), artifact_id)
                write(dst / 'src/main/resources/META-INF/web-fragment.xml', frag)
                report.append(f'  [xml] {bp.group(1)} : {n} servlets OSGi -> META-INF/web-fragment.xml')
                corpus.append('restConfiguration()')  # the fragment needs camel-servlet on the module classpath
                continue
            before = len(report)
            converted = blueprint.convert(text, bp.group(1), report, compat)
            info['manual'] += [line.strip() for line in report[before:] if '[MANUAL]' in line]
            write(dst / 'src/main/resources' / module_dir / bp.group(1), converted)
            corpus.append(converted)
            info['xml'].append(converted)
            continue
        out = dst / 'src/main' / rel
        if f.suffix == '.java':
            orig = read(f)
            text = apply_java_rules(orig, rules, compat, f.name, report)
            text, n = apply_patches(text, cfg.get('patches', []), artifact_id, f.name, report)
            totals['patches'] += n
            for r in rules['manualPatterns']:
                n = len(r['_re'].findall(text))
                if n:
                    line = f"[MANUAL] {f.name} : {r['id']} x{n} ({r['description']})"
                    report.append('  ' + line)
                    info['manual'].append(line)
            n = nested_end_choice(text)
            if n:
                line = f'[MANUAL] {f.name} : choice anidado con endChoice() x{n} (verificar a qué choice pertenece lo que sigue)'
                report.append('  ' + line)
                info['manual'].append(line)
            info['javaFiles'] += 1
            if text != orig:
                info['javaChanged'] += 1
                totals['javaLines'] += sum((Counter(text.split('\n')) - Counter(orig.split('\n'))).values())
            write(out, text)
            corpus.append(text)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, out)
    all_text = ''.join(corpus)
    names = header_names(all_text, main)
    if names:
        write(dst / 'src/main/resources/META-INF/mf/http-headers.txt', '\n'.join(names) + '\n')
        report.append(f"  [gen] http-headers.txt : {len(names)} nombres ({', '.join(names)})")
    cont = cfg.get('containers')
    if cont and info['xml']:  # where the bundle is deployed today -> the loader filters with -Dmf.container=<name>
        ci = src / cont.get('file', '.gitlab-ci.yml')
        ci_text = read(ci) if ci.exists() else ''
        keep = [c for c in cont['names'] if not re.search(cont['skipPattern'].replace('{NAME}', c.upper()), ci_text, re.I)]
        write(dst / 'src/main/resources' / module_dir / 'module.properties',
              f"# GENERADO por Migration Factory a partir de {cont.get('file', '.gitlab-ci.yml')}{NL}containers={','.join(keep)}{NL}")
        info['containers'] = keep
        if len(keep) < len(cont['names']):
            report.append(f"  [gen] module.properties : solo en {', '.join(keep)}")
    pom, deps = module_pom(orig_pom, all_text, artifact_id, rules, cfg['groupId'], cfg['version'])
    write(dst / 'pom.xml', pom)
    info['dependencies'] = deps
    totals['javaFiles'] += info['javaFiles']
    totals['javaChanged'] += info['javaChanged']
    totals['manual'] += len(info['manual'])
    return info


def _template(out: Path, rules: dict, cfg: dict):
    p = rules['profile']
    extra = ''
    for d in cfg.get('extraWarDependencies', []):
        parts = d.split(':')
        if len(parts) not in (2, 3):
            raise InvalidInput(f'extraWarDependencies: se esperaba g:a o g:a:v, no {d!r}')
        extra += _dep_xml(*parts).replace('\r\n', '\n')
    tokens = {'@GROUP_ID@': cfg['groupId'], '@VERSION@': cfg['version'], '@JAVA_RELEASE@': p['javaRelease'], '@CAMEL_VERSION@': p['camelVersion'],
              '@SPRING_VERSION@': p['springVersion'], '@CXF_VERSION@': p['cxfVersion'], '@SLF4J_VERSION@': p['slf4jVersion'],
              '@TOMCAT_VERSION@': p['tomcatVersion'], '@ACTIVEMQ_VERSION@': p['activemqVersion'], '@EXTRA_WAR_DEPENDENCIES@': extra}
    src = RULES_DIR / 'template'
    for f in sorted(x for x in src.rglob('*') if x.is_file()):
        text = f.read_text(encoding='utf-8')
        for k, v in tokens.items():
            text = text.replace(k, v)
        dst = out / f.relative_to(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding='utf-8', newline='')


def load_config(path: Path | None) -> dict:
    cfg = json.loads(Path(path).read_text(encoding='utf-8')) if path else {}
    cfg.setdefault('groupId', 'org.migrationfactory.generated')
    cfg.setdefault('version', '1.0.0-SNAPSHOT')
    for p in cfg.get('patches', []):
        missing = {'name', 'module', 'file', 'find', 'replace'} - set(p)
        if missing:
            raise InvalidInput(f"Parche incompleto ({', '.join(sorted(missing))}): {p.get('name', p)}")
        if p.get('regex'):
            re.compile(p['find'])
    c = cfg.get('containers')
    if c and not (c.get('names') and c.get('skipPattern')):
        raise InvalidInput('containers requiere names y skipPattern')
    return cfg


def build(workspace: Path, log=lambda m: None, timeout: int = 3600) -> dict:
    """`mvn -DskipTests package` on the migrated workspace. Compiling proves the POMs and the Camel 4 API usage; it does not
    prove behaviour (see docs/16_EQUIVALENCIA.md)."""
    import subprocess
    mvn = shutil.which('mvn')
    if not mvn:
        return {'status': 'NOT_RUN', 'detail': 'mvn no está en el PATH'}
    log_file = workspace.parent / 'logs' / 'build.log'
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log(f'compilando {workspace} (log: {log_file})')
    with log_file.open('wb') as fh:
        try:
            code = subprocess.run([mvn, '-B', '-q', '-DskipTests', 'package'], cwd=workspace, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            return {'status': 'FAIL', 'detail': f'timeout de {timeout}s', 'log': str(log_file)}
    errors = [line for line in log_file.read_text(encoding='utf-8', errors='replace').splitlines() if line.startswith('[ERROR]')][:20]
    return {'status': 'PASS' if code == 0 else 'FAIL', 'exitCode': code, 'log': str(log_file), 'errors': errors}


def migrate(source: Path, out: Path, config: Path | None = None, log=lambda m: None) -> dict:
    source, out = Path(source).resolve(), Path(out).resolve()
    if not source.is_dir():
        raise InvalidInput(f'No es un directorio: {source}')
    if out == source or source in out.parents:
        raise InvalidInput('La salida no puede estar dentro del origen (el origen nunca se modifica)')
    rules, cfg = load_rules(), load_config(config)
    compat = rules['compatPackage']
    projects = discover(source)
    if not projects:
        raise InvalidInput(f'No se encontraron proyectos Maven (pom.xml + src/main) en {source}')
    ws = out / 'migrated-workspace'
    if ws.exists():
        shutil.rmtree(ws)
    report, modules, totals = [], [], {'javaFiles': 0, 'javaChanged': 0, 'javaLines': 0, 'xmlFiles': 0, 'manual': 0, 'patches': 0}
    seen = {}
    for src in projects:
        rel = src.relative_to(source).as_posix()
        artifact_id = _head(read(src / 'pom.xml'), 'artifactId')
        if artifact_id in seen:
            raise InvalidInput(f'artifactId repetido en el workspace: {artifact_id} ({seen[artifact_id]} y {rel})')
        seen[artifact_id] = rel
        report.append(f'== {rel}  ->  {artifact_id}')
        log(f'migrando {rel}')
        info = migrate_module(src, ws / 'modules' / artifact_id, rules, cfg, compat, report, totals)
        modules.append(info | {'source': rel})
    declared = {(p['module'], p['file']) for p in cfg.get('patches', [])}
    unused = sorted(declared - _patched(report))
    if unused:
        raise PatchMismatch('Parches que no encontraron su archivo: ' + ', '.join(f'{m}/{f}' for m, f in unused))
    g, v = cfg['groupId'], cfg['version']
    all_deps = ''.join(_dep_xml(m['groupId'], m['artifactId'], m['version']) for m in modules)
    write(ws / 'modules/migrated-modules-all/pom.xml',
          _aggregator(g, v, 'migrated-modules', 'migrated-modules-all', 'Todos los modulos migrados', f'\t<dependencies>\n{all_deps}\t</dependencies>'))
    names = [m['artifactId'] for m in modules] + ['migrated-modules-all']
    write(ws / 'modules/pom.xml', _aggregator(g, v, 'migrated-parent', 'migrated-modules', 'Modulos migrados',
                                               '\t<modules>\n' + '\n'.join(f'\t\t<module>{n}</module>' for n in names) + '\n\t</modules>'))
    _template(ws, rules, cfg)
    overlay = cfg.get('overlay')
    if overlay:  # client additions (extra web.xml entries, datasources of the target environment...): copied last, over the result
        ov = (Path(config).resolve().parent / overlay).resolve()
        if not ov.is_dir():
            raise InvalidInput(f'overlay no es un directorio: {ov}')
        shutil.copytree(ov, ws, dirs_exist_ok=True)
    unresolved = blueprint.unresolved_imports({m['artifactId']: m.pop('xml') for m in modules})
    manual = [f"{m['artifactId']}: {line}" for m in modules for line in m['manual']]
    result = {
        'profile': rules['profile']['key'], 'rulesVersion': rules['version'], 'source': str(source), 'output': str(ws),
        'status': 'PARTIAL_SUCCESS' if manual or unresolved else 'SUCCEEDED',
        'totals': totals | {'modules': len(modules)}, 'modules': modules, 'manualActions': manual, 'unresolvedServices': unresolved,
    }
    reports = out / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    (reports / 'tomcat-war-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (reports / 'tomcat-war-report.md').write_text(report_markdown(result, report), encoding='utf-8')
    return result


def _patched(report) -> set:
    """(module, file) pairs for which at least one patch was applied, read back from the per-module report lines."""
    out, module = set(), None
    for line in report:
        if line.startswith('== '):
            module = line.rsplit('->', 1)[1].strip()
        elif line.startswith('  [parche] '):
            out.add((module, line[len('  [parche] '):].split(' : ', 1)[0]))
    return out


def report_markdown(result: dict, lines: list) -> str:
    t = result['totals']
    out = ['# Migración a Tomcat WAR (Camel 4 + Spring)', '', f"Perfil `{result['profile']}`, reglas {result['rulesVersion']}. Estado: **{result['status']}**.", '',
           '| Medida | Valor |', '|---|---|', f"| Módulos | {t['modules']} |", f"| Archivos Java | {t['javaFiles']} |",
           f"| Archivos Java modificados | {t['javaChanged']} |", f"| Líneas Java modificadas | {t['javaLines']} |",
           f"| XML Blueprint convertidos | {t['xmlFiles']} |", f"| Parches manuales aplicados | {t['patches']} |",
           f"| Puntos de revisión manual | {t['manual']} |", '']
    if result['unresolvedServices']:
        out += ['## Servicios importados que ningún módulo exporta', '',
                'Existen en lo desplegado pero no en el origen analizado: hay que obtenerlos del cliente.', '',
                '| Módulo | Interfaz | component-name |', '|---|---|---|']
        out += [f"| {u['module']} | `{u['interface']}` | {u['componentName'] or '—'} |" for u in result['unresolvedServices']] + ['']
    if result['manualActions']:
        out += ['## Revisión manual', ''] + [f'- {m}' for m in result['manualActions']] + ['']
    out += ['## Detalle por módulo', '', '```text'] + lines + ['```', '']
    return '\n'.join(out)

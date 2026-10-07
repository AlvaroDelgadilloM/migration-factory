"""Pre-flight (camel-migration-tool-docs 2 / 14-PREFLIGHT-BASELINE-REPAIR.md).

ENVIRONMENT CHECK -> BUILD MODEL VALIDATION -> BASELINE REPAIR -> BASELINE BUILD, all on the isolated copy.
The baseline repair only makes the *legacy* build readable (e.g. import the Camel 2 BOM for versionless Camel
dependencies). It never changes components to Camel 4: that belongs to the migration.
"""
import hashlib
import os
import platform
import re
import time
import urllib.request
from pathlib import Path

from .analysis import maven as mvn_model
from .analysis import xmlparse
from .config import get_settings

CATEGORIES = ('ENVIRONMENT', 'BUILD_MODEL', 'DEPENDENCY', 'SOURCE', 'MIGRATION', 'TEST', 'SECURITY', 'TOOLING')
STATES = ('BASELINE_SUCCESS', 'BASELINE_REPAIRED', 'BASELINE_FAILED_CODE', 'BASELINE_FAILED_TEST', 'BASELINE_BLOCKED', 'BASELINE_BLOCKED_DEPENDENCY',
          'BASELINE_BLOCKED_REPOSITORY', 'BASELINE_BLOCKED_AUTH', 'BASELINE_BLOCKED_NETWORK', 'BASELINE_BLOCKED_INTERNAL_ARTIFACT', 'BASELINE_BLOCKED_PLATFORM_BOM',
          'BASELINE_UNKNOWN')

# Maven/compiler output -> category. Order matters: first match wins.
_CLASSIFIERS = [
    ('ORCHESTRATION_ERROR', r'MAVEN_EXECUTED_ON_NON_PROJECT_ROOT'),
    ('CANDIDATE_BUILD_CONFIGURATION_ERROR', r'MissingProjectException|there is no POM in this directory|MISSING_CANDIDATE_POM|MISSING_CANDIDATE_DIRECTORY'),
    ('TOOLING', r'Failed to execute goal org\.openrewrite|RecipeRunException|OutOfMemoryError|No plugin found for prefix'),
    ('ENVIRONMENT', r'jansi.*(?:not executable|failed to map segment|Failed to load native library)|noexec'),
    ('ENVIRONMENT', r'Unsupported class file major version|release version \d+ not supported|invalid target release|JAVA_HOME is not defined'),
    ('BUILD_MODEL', r"'dependencies\.dependency\.version' for [^ ]+ is missing"),
    ('BUILD_MODEL', r'ProjectBuildingException|Non-resolvable parent POM|Non-resolvable import POM|Unknown packaging|The build could not read'),
    ('DEPENDENCY', r'Could not resolve dependencies|Could not find artifact|Could not transfer artifact|Plugin .* could not be resolved'),
    ('SOURCE', r'COMPILATION ERROR|cannot find symbol|package [\w.]+ does not exist|incompatible types|cannot be applied to given types'),
    ('TEST', r'There are test failures|Tests run: \d+, Failures: [1-9]|Tests run: \d+, Failures: \d+, Errors: [1-9]|FailedToCreateRouteException'),
]


AUTO_FIXABLE = [  # categories/patterns with a known, evidence-based fix (baseline repair or candidate autofix)
    r"'dependencies\.dependency\.version' for org\.apache\.camel:",
    r'package javax\.(?:jms|inject|ws\.rs|persistence|servlet|validation|xml\.bind)\b[\w.]* does not exist',
    r'No endpoint could be found for|No component found with scheme',
]


REMEDIATION = [  # known causes -> actionable hint (shown in reports; nothing is changed automatically)
    (r'Could not find artifact org\.opensaml:', 'opensaml no está en Maven Central (lo arrastra CXF WS-Security). Apruebe el repositorio '
     'Shibboleth: MF_MAVEN_EXTRA_REPOS="shibboleth|https://build.shibboleth.net/maven/releases", o excluya opensaml si no usa SAML.'),
    (r"'dependencies\.dependency\.version' for org\.apache\.camel:", 'Importar camel-bom de la versión Camel del proyecto (baseline repair).'),
]


def enrich(errors: list[dict], stage: str) -> list[dict]:
    """Adds stage (BASELINE | CANDIDATE), blocking and autoFixable to classified errors."""
    return [e | {'stage': stage, 'blocking': e['category'] in ('ENVIRONMENT', 'BUILD_MODEL', 'DEPENDENCY', 'SOURCE', 'MIGRATION', 'TOOLING', 'CANDIDATE_BUILD_CONFIGURATION_ERROR', 'ORCHESTRATION_ERROR'),
                 'autoFixable': any(re.search(rx, e['message']) for rx in AUTO_FIXABLE),
                 'remediation': next((h for rx, h in REMEDIATION if re.search(rx, e['message'])), None)} for e in errors]


def model_check(run_mvn, project, name) -> dict:
    """MAVEN MODEL CHECK: can Maven build the project model? (mvn -B -e validate). run_mvn(project, goals, name) -> Result."""
    try:
        res = run_mvn(project, ['validate'], name)
    except FileNotFoundError:
        return {'status': 'UNKNOWN', 'exitCode': 127, 'errors': [{'category': 'TOOLING', 'message': 'mvn no encontrado', 'fingerprint': 'mvn-missing',
                                                                  'stage': 'BASELINE', 'blocking': True, 'autoFixable': False}]}
    text = res.log_path.read_text(errors='replace')
    return {'status': 'VALID' if res.exit_code == 0 else 'INVALID', 'exitCode': res.exit_code, 'log': str(res.log_path),
            'command': 'mvn -B -e -Dstyle.color=never validate', 'errors': enrich(classify(text), 'BASELINE')}


def classify(log_text: str) -> list[dict]:
    """Distinct ERROR lines with a category and a fingerprint (to tell baseline errors from migration errors)."""
    out, seen = [], set()
    for line in log_text.splitlines():
        if not re.search(r'ERROR|FAIL|Exception|jansi|not executable', line, re.I):
            continue
        msg = re.sub(r'\s+', ' ', re.sub(r'^\[(?:ERROR|INFO|WARNING)\]\s*', '', line)).strip()
        if not msg or msg in seen or msg.startswith(('->', 'Re-run Maven', 'For more information', 'To see the full stack')):
            continue
        cat = next((c for c, rx in _CLASSIFIERS if re.search(rx, msg, re.I)), None)
        if cat is None:
            continue
        seen.add(msg)
        norm = re.sub(r'/[\w./\-]+/(src|target)/', r'\1/', msg)  # path-independent fingerprint
        norm = re.sub(r'\d+', 'N', norm)
        out.append({'category': cat, 'message': msg[:400], 'fingerprint': hashlib.sha1(norm.encode()).hexdigest()[:12]})
    return out


# --- 1. environment -----------------------------------------------------------------------------
def jansi_tmp(workdir: Path) -> Path:
    p = Path(workdir) / '.migration-tmp' / 'jansi'
    p.mkdir(parents=True, exist_ok=True)
    return p


def environment(ctx, run) -> list[dict]:
    """run(args, name) -> (exit_code, text). Checks only: nothing is installed or changed."""
    checks = []

    def add(check, status, detail):
        checks.append({'check': check, 'status': status, 'detail': detail, 'category': 'ENVIRONMENT'})

    code, out = run(['java', '-version'], 'env-java')
    m = re.search(r'version "((\d+)(?:\.(\d+))?[^"]*)"', out)
    major = (int(m[3] or 0) if m[2] == '1' else int(m[2])) if m else 0
    add('java', 'PASS' if code == 0 and major >= 17 else 'FAIL', f'{m[1] if m else "no encontrado"} (se requiere ≥ 17 para el destino)')
    code, out = run(['mvn', '-v'], 'env-maven')
    mv = re.search(r'Apache Maven ([\d.]+)', out)
    jv = re.search(r'Java version: ([\w.\-]+)', out)
    add('maven', 'PASS' if code == 0 and mv else 'FAIL', f"Maven {mv[1] if mv else 'no encontrado'} con Java {jv[1] if jv else '?'}")
    if jv and m and jv[1].split('.')[0] != m[1].split('.')[0]:
        add('java-maven', 'WARN', f'Maven usa Java {jv[1]} y `java` es {m[1]}: fije JAVA_HOME')
    add('arch', 'PASS', f'{platform.system()} {platform.machine()}')
    tmp = jansi_tmp(ctx.workdir)
    probe = tmp / 'probe.sh'
    try:
        probe.write_text('#!/bin/sh\necho ok\n')
        probe.chmod(0o700)
        code, out = run([str(probe)], 'env-tmp-exec')
        add('tmp', 'PASS' if code == 0 and 'ok' in out else 'WARN',
            f'tmp de trabajo {tmp} ' + ('escribible y ejecutable' if code == 0 else 'no ejecutable (noexec): Jansi usará este directorio igualmente'))
    except OSError as e:
        add('tmp', 'FAIL', f'tmp de trabajo no escribible: {e}')
    finally:
        probe.unlink(missing_ok=True)
    sys_tmp = Path(os.environ.get('TMPDIR', '/tmp'))
    add('system-tmp', 'PASS' if os.access(sys_tmp, os.W_OK) else 'WARN', f'{sys_tmp} {"escribible" if os.access(sys_tmp, os.W_OK) else "no escribible"}')
    import shutil as _sh
    free = _sh.disk_usage(ctx.workdir).free
    add('disk', 'PASS' if free > 2 * 1024 ** 3 else 'WARN', f'{free // 1024 ** 2} MB libres en el workspace')
    add('workspace', 'PASS' if os.access(ctx.workdir, os.W_OK) else 'FAIL', f'{ctx.workdir} {"escribible" if os.access(ctx.workdir, os.W_OK) else "sin permiso de escritura"}')
    add('JAVA_HOME', 'PASS' if os.environ.get('JAVA_HOME') else 'WARN', os.environ.get('JAVA_HOME') or 'no definido (se usa el java del PATH)')
    add('MAVEN_OPTS', 'PASS', f"jansi.tmpdir={tmp} (siempre se fija en el workspace)")
    mirror = get_settings().maven_mirror_url
    try:
        with urllib.request.urlopen(urllib.request.Request(mirror.rstrip('/') + '/org/apache/maven/', method='HEAD'), timeout=8) as r:
            add('maven-repo', 'PASS', f'{mirror} accesible ({r.status})')
    except Exception as e:  # offline is allowed if the local repository already has what is needed
        add('maven-repo', 'WARN', f'{mirror} no accesible: {type(e).__name__}')
    return checks


# --- 2. build model -----------------------------------------------------------------------------
def validate_model(root: Path, modules: list[dict], errors: list[dict]) -> list[dict]:
    """POM-### findings: unparsable POMs, versionless dependencies, unresolved properties/parents, missing modules."""
    out, n = [], 0

    def add(**f):
        nonlocal n
        n += 1
        out.append({'id': f'POM-{n:03d}', 'category': 'BUILD_MODEL'} | f)
    for e in errors:
        if e['code'] in ('INVALID_POM', 'INVALID_XML') and e['file'].endswith('pom.xml'):
            add(severity='CRITICAL', file=e['file'], message='POM no parseable', autoFix=False)
    for m in modules:
        bom_groups = {b.split(':')[0] for b in m.get('boms', [])}
        for d in m['dependencies']:
            if d['versionSource'] != 'unresolved':
                continue
            if d['groupId'] in bom_groups:  # an imported (external) BOM of the same group will manage it: Maven resolves it
                add(severity='INFO', file=m['file'], line=d['line'], artifact=f"{d['groupId']}:{d['artifactId']}",
                    message='Sin versión; cubierta por un BOM importado externo (se resuelve con Maven)', autoFix=False, baseline='OK')
            else:
                add(severity='HIGH', file=m['file'], line=d['line'], artifact=f"{d['groupId']}:{d['artifactId']}",
                    message='Dependencia sin versión y no cubierta por dependencyManagement/BOM', autoFix=False)
        for u in m['unresolved']:
            if u.startswith('parent'):
                add(severity='HIGH', file=m['file'], message=u, autoFix=False)
        for mod in m.get('modules', []):
            if not (root / Path(m['file']).parent / mod / 'pom.xml').exists():
                add(severity='HIGH', file=m['file'], message=f'Módulo declarado inexistente: {mod}', autoFix=False)
    return out


# --- 3. baseline repair -------------------------------------------------------------------------
def _artifact_exists(group, artifact, version) -> bool:
    base = get_settings().maven_mirror_url.rstrip('/')
    url = f"{base}/{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.pom"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def camel_version(module: dict, pom_props: dict) -> str | None:
    v = pom_props.get('camel.version')
    if v and not v.startswith('${'):
        return v
    vs = {d['versionResolved'] for d in module['dependencies'] if d['groupId'] == 'org.apache.camel' and d['versionResolved']}
    return vs.pop() if len(vs) == 1 else None


def repair(root: Path, modules: list[dict], findings: list[dict], exists=_artifact_exists) -> list[dict]:
    """Import org.apache.camel:camel-bom:<legacy version> where Camel dependencies have no version.
    Evidence-based only: the Camel 2 version must be unambiguous and the BOM must exist in the repository."""
    repairs = []
    for m in modules:
        missing = [d for d in m['dependencies'] if d['groupId'] == 'org.apache.camel' and d['versionSource'] == 'unresolved']
        if not missing or any(b.startswith('org.apache.camel:camel-bom:') for b in m.get('boms', [])):
            continue  # nothing to repair, or the Camel BOM is already imported
        pom_path = root / m['file']
        text = pom_path.read_text(encoding='utf-8')
        tree = xmlparse.parse(text)
        props = {c.tag: c.text.strip() for c in (tree.first('properties').children if tree.first('properties') else [])}
        version = camel_version(m, props)
        related = [f for f in findings if f.get('artifact') in {f"org.apache.camel:{d['artifactId']}" for d in missing}]
        if not version or not version.startswith('2.'):
            for f in related:
                f['fix'] = 'Versión Camel 2 no identificable de forma inequívoca: declarar camel.version manualmente'
            continue
        if not exists('org.apache.camel', 'camel-bom', version):
            for f in related:
                f['fix'] = f'org.apache.camel:camel-bom:{version} no verificable en el repositorio: no se repara'
            continue
        before = hashlib.sha256(text.encode()).hexdigest()
        lines = text.split('\n')
        bom = ('      <dependency>\n        <groupId>org.apache.camel</groupId>\n        <artifactId>camel-bom</artifactId>\n'
               '        <version>${camel.version}</version>\n        <type>pom</type>\n        <scope>import</scope>\n      </dependency>')
        dm = tree.first('dependencyManagement/dependencies')
        if dm is not None:
            lines.insert(dm.line, bom)  # right after <dependencies> inside <dependencyManagement>
        else:
            deps = tree.first('dependencies')
            lines.insert(deps.line - 1, '  <dependencyManagement>\n    <dependencies>\n' + bom + '\n    </dependencies>\n  </dependencyManagement>\n')
        if 'camel.version' not in props:
            p = tree.first('properties')
            if p is not None:
                lines.insert(p.line, f'    <camel.version>{version}</camel.version>')
            else:
                first = (tree.first('dependencyManagement') or tree.first('dependencies'))
                lines.insert(first.line - 1, f'  <properties>\n    <camel.version>{version}</camel.version>\n  </properties>')
        new = '\n'.join(lines)
        try:
            check = xmlparse.parse(new)  # must still be valid XML with the BOM imported at project level
        except ValueError:
            for f in related:
                f['fix'] = 'Formato de POM no editable de forma segura: importar camel-bom manualmente'
            continue
        managed = [(d.findtext('artifactId'), d.findtext('scope')) for d in check.find('dependencyManagement/dependencies/dependency')]
        if ('camel-bom', 'import') not in managed:
            continue
        pom_path.write_text(new, encoding='utf-8')
        repairs.append({'file': m['file'], 'kind': 'import-camel-bom', 'category': 'BUILD_MODEL',
                        'change': f'Importado org.apache.camel:camel-bom:{version} en dependencyManagement'
                                  + ('' if 'camel.version' in props else f' y declarado camel.version={version}'),
                        'basis': f'Camel {version} identificado en el POM; camel-bom {version} verificado en {get_settings().maven_mirror_url}',
                        'covers': [f"org.apache.camel:{d['artifactId']}" for d in missing], 'before': before,
                        'after': hashlib.sha256(new.encode()).hexdigest()})
        for f in related:
            f['autoFix'], f['fix'], f['baseline'] = True, f'Import org.apache.camel:camel-bom:{version}', 'FIXED'
    return repairs


# --- 4. baseline build + state ------------------------------------------------------------------
def baseline_goals(root: Path) -> list[str]:
    has_tests = any(p.suffix == '.java' for p in root.glob('**/src/test/java/**/*.java'))
    return ['clean', 'test'] if has_tests else ['clean', 'compile']


def state(build_ok: bool, repairs, errors, issues=()) -> str:
    """Doc 32: code failures (BASELINE_FAILED_CODE/TEST) vs a baseline the environment cannot reproduce (BASELINE_BLOCKED_*)."""
    from .baseline.dependency_resolution_classifier import baseline_state
    return baseline_state(build_ok, repairs, errors, list(issues))


def report_markdown(env, model, repairs, build, model_check=None) -> str:
    rows = lambda items, cols: '\n'.join('| ' + ' | '.join(str(i.get(c, '')).replace('|', '\\|') for c in cols) + ' |' for i in items)
    out = ['# Baseline (pre-flight)', '', f"**Estado:** {build.get('state', 'NOT_RUN')}", '',
           '## Ambiente', '| Check | Estado | Detalle |', '|---|---|---|', rows(env, ('check', 'status', 'detail')), '',
           '## Maven model check (`mvn -B -e validate`)',
           f"Antes de reparar: {((model_check or {}).get('before') or {}).get('status', 'NOT_RUN')} · después: {((model_check or {}).get('after') or {}).get('status', 'NOT_RUN')}"
           + (f" · motivo: {build.get('reason')}" if build.get('reason') else ''), '',
           '## Modelo Maven (estático)', ('| ID | Severidad | Archivo | Artefacto | Mensaje | Corrección |\n|---|---|---|---|---|---|\n'
                               + rows(model, ('id', 'severity', 'file', 'artifact', 'message', 'fix'))) if model else 'Sin problemas.', '',
           '## Reparaciones aplicadas (solo en la copia de trabajo)',
           ('| Archivo | Cambio | Evidencia |\n|---|---|---|\n' + rows(repairs, ('file', 'change', 'basis'))) if repairs else 'Ninguna.', '',
           '## Build de referencia']
    if build.get('command'):
        out += [f"Comando: `{build['command']}` · exit {build['exitCode']} · {build.get('durationMs', 0) / 1000:.1f} s · pruebas {build.get('tests')}",
                '', '| Categoría | Error |', '|---|---|', rows(build.get('errors', []), ('category', 'message'))]
    else:
        out.append('No ejecutado.')
    out += ['', 'La reparación de baseline NO migra componentes (p. ej. camel-http4 se conserva); eso pertenece a la migración.']
    return '\n'.join(out) + '\n'

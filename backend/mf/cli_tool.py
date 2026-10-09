"""camel-migration-tool: offline CLI over the Migration Factory engine (no database, queue or Docker).

    python migrate.py analyze  <project>
    python migrate.py plan     <project> --target springboot
    python migrate.py migrate  <project> --target springboot --output <path>
    python migrate.py validate <migrated-project>

Same scanner, rules, profiles, recipes and validations as the platform. The source is never modified:
`migrate` works on a copy and writes <output>/migrated-project, reports/ and logs/.
Exit codes: 0 ok · 1 analysis/migration error · 2 build failed · 3 critical manual action (--fail-on-critical) · 4 invalid input.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

EXIT_OK, EXIT_ERROR, EXIT_BUILD, EXIT_CRITICAL, EXIT_INPUT = 0, 1, 2, 3, 4
TARGETS = {'springboot': 'spring', 'spring': 'spring', 'quarkus': 'quarkus', 'tomcat-war': 'tomcat-war'}
ACTION = {'AUTO': 'AUTOMATIC', 'AUTO_TEST': 'AUTOMATIC_WITH_REVIEW', 'REVIEW': 'AUTOMATIC_WITH_REVIEW', 'MANUAL': 'MANUAL'}
JAKARTA_FIX = {  # compile error "package javax.X does not exist" -> verified Jakarta recipe
    'javax.jms': 'org.openrewrite.java.migrate.jakarta.JavaxJmsToJakartaJms',
    'javax.inject': 'org.openrewrite.java.migrate.jakarta.JavaxInjectMigrationToJakartaInject',
    'javax.ws.rs': 'org.openrewrite.java.migrate.jakarta.JavaxWsToJakartaWs',
    'javax.persistence': 'org.openrewrite.java.migrate.jakarta.JavaxPersistenceToJakartaPersistence',
    'javax.servlet': 'org.openrewrite.java.migrate.jakarta.JavaxServletToJakartaServlet',
    'javax.validation': 'org.openrewrite.java.migrate.jakarta.JavaxValidationMigrationToJakartaValidation',
    'javax.xml.bind': 'org.openrewrite.java.migrate.jakarta.JavaxXmlBindMigrationToJakartaXmlBind',
}


class InvalidInput(Exception):
    pass


_PARENT = {'ctx': None}  # set by the platform worker: its job context governs every engine call
_WORKSPACE = {'internal': frozenset()}  # g:a of every project of the workspace being migrated (doc 32 §10)


def engine_context(workdir: Path, hours: int):
    """Context for an engine run. Standalone CLI: own deadline. Inside a platform job: same job id, deadline, cancellation,
    secrets and live log as the job, so cancelling or timing out the job stops the whole process group of every step."""
    from .worker import runner
    p = _PARENT['ctx']
    if p is None:
        return runner.Context('cli', workdir, deadline=time.monotonic() + hours * 3600, emit=None)
    return runner.Context(p.job_id, workdir, deadline=p.deadline, cancel=p.cancel, secrets=p.secrets, emit=p.emit)


def _env_for_cli():
    """Settings for a local, single-user run. Must run before mf.config is imported."""
    os.environ.setdefault('MF_AUTH_MODE', 'dev')
    os.environ.setdefault('MF_DEV_JWT_SECRET', 'cli-' + uuid.uuid4().hex * 2)  # unused by the CLI, required by settings
    os.environ.setdefault('MF_MAVEN_REPO_LOCAL', str(Path.home() / '.m2' / 'repository'))
    tools = [shutil.which(t) for t in ('mvn', 'git', 'java')]
    dirs = [str(Path(t).resolve().parent) for t in tools if t] + [str(Path(t).parent) for t in tools if t]
    os.environ.setdefault('MF_TOOL_PATH', ':'.join(dict.fromkeys(dirs + ['/usr/local/bin', '/usr/bin', '/bin'])))


# --- analyze ------------------------------------------------------------------------------------
def analyze(source: Path, target: str, out: Path | None, effective: bool, log, work: Path | None = None,
            build: bool = False):
    """Pre-flight + static inventory. Findings come from the ORIGINAL source (line numbers match it); the effective
    POM and the baseline build run on an isolated copy (`work`, or a temporary one) after the baseline repair."""
    from . import preflight
    from .analysis.scanner import apply_effective, load_catalog, scan
    from .worker import mavenops, runner, source as src_mod
    if not source.is_dir():
        raise InvalidInput(f'No es un directorio: {source}')
    files = [p.relative_to(source).as_posix() for p in src_mod._walk(source) if p.name == 'pom.xml']
    from .security import UnsafeInput, find_project_root
    try:
        root_rel = find_project_root(files)
    except UnsafeInput as e:
        raise InvalidInput(str(e)) from None
    root = source if root_rel == '.' else source / root_rel
    catalog = load_catalog()
    report = scan(root, target, catalog)
    report['projectRoot'] = root_rel
    report['errors'] += [{'file': f, 'code': 'SENSITIVE_FILE_EXCLUDED'} for f in src_mod.sensitive_files(root)]
    report['snapshotHash'] = src_mod.content_hash(root)
    report['mavenResolution'] = 'skipped'
    pf = {'environment': [], 'model': preflight.validate_model(root, report['modules'], report['errors']), 'repairs': [],
          'modelCheck': {}, 'build': {'state': 'NOT_RUN'}}
    if effective:
        tmp = None if work else (out or Path(os.environ.get('TMPDIR', '/tmp'))) / f'.mf-analyze-{uuid.uuid4().hex[:8]}'
        baseline_dir = work or tmp / 'baseline'
        ctx = engine_context(out or tmp, 3)
        try:
            report = run_baseline(root, baseline_dir, ctx, report, pf, catalog, build, log)
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
    report['preflight'] = pf
    report['baseline'] = {'status': pf['build'].get('state'), 'reason': pf['build'].get('reason'),
                          'mavenModel': {k: v.get('status') for k, v in pf['modelCheck'].items()},
                          'buildExitCode': pf['build'].get('exitCode'),
                          'environmentWarnings': [c for c in pf['environment'] if c['status'] != 'PASS'],
                          'repairs': pf['repairs'], 'buildModelFindings': pf['model'],
                          # doc 42 §14: a vendor BOM blocks the Maven model, not the static analysis
                          'analysisStatus': 'PARTIAL_SUCCESS' if pf['build'].get('state') == 'BASELINE_BLOCKED_PLATFORM_BOM' else None}
    mods = report['modules']
    javas = sorted({m['javaResolved'] or m['javaObserved'] for m in mods if m['javaResolved'] or m['javaObserved']})
    camels = sorted({v for m in mods for v in m['camelVersions'] if v != '?'})
    jboss = any(f['ruleId'].startswith('JBOSS') or f['ruleId'] in ('JNDI', 'EJB') for f in report['findings'])
    rules = {f['ruleId'] for f in report['findings']}
    reasons = []
    if 'SPRING_XML' in rules:
        reasons.append('usa Spring XML → Spring Boot conserva el modelo de beans con menos cambios')
    if rules & {'CDI_SCOPE', 'CAMEL_CDI', 'JAKARTA_INJECT'}:
        reasons.append('usa CDI → Quarkus (ArC) es CDI; en Spring Boot @Inject funciona pero los ámbitos CDI se reescriben')
    report['overview'] = {
        'java': javas, 'camel': camels, 'runtime': 'jboss' if jboss else 'unknown', 'sourcePlatform': (report.get('platform') or {}).get('platform'),
        'packaging': sorted({m['packaging'] for m in mods}),
        'dependencies': sorted({f"{d['groupId']}:{d['artifactId']}:{d['versionResolved'] or '?'}" for m in mods for d in m['dependencies']}),
        'findingsByCategory': {c: sum(f.get('category') == c for f in report['findings']) for c in sorted({f.get('category') for f in report['findings']})},
        'targetConsiderations': reasons or ['sin señales que favorezcan un runtime: decidir por estándar de la organización'],
    }
    return report


def baseline_reason(errors, compile_status) -> str:
    """Doc 30: infrastructure is told apart from code (a missing artifact is not a source error)."""
    cats = {e['category'] for e in errors}
    for cat, code in (('ORCHESTRATION_ERROR', 'ORCHESTRATION_ERROR'), ('ENVIRONMENT', 'ENVIRONMENT'), ('BUILD_MODEL', 'BUILD_MODEL'),
                      ('DEPENDENCY', 'DEPENDENCY_RESOLUTION_ERROR'), ('TOOLING', 'TOOLING')):
        if cat in cats:
            return code
    return 'SOURCE' if compile_status != 'PASS' else 'TEST'


def _platform(report, pf):
    """Doc 42: PlatformDetector + VendorBomAnalyzer with the model-check errors of the (repaired) baseline."""
    from .baseline import vendor_bom_analyzer as vba
    mirror, repos = vba.configured_repositories()
    plat = vba.assess(report['modules'], report.get('usage'), (pf['modelCheck'].get('after') or {}).get('errors', []), pf['repairs'], repos, mirror)
    pf['platform'] = report['platform'] = plat
    return plat


def run_baseline(root, baseline_dir, ctx, report, pf, catalog, build, log):
    """SOURCE COPY -> ENVIRONMENT -> MAVEN MODEL CHECK -> POM REPAIR -> REVALIDATION -> (effective POM) -> COMPILE -> TEST.
    Works only on baseline_dir. Fills pf; returns the report (possibly enriched with the effective POM)."""
    from . import preflight
    from .analysis.scanner import apply_effective
    from .worker import gitops, mavenops, runner, source as src_mod

    def run(args, name):
        try:
            r = runner.run(args, baseline_dir, ctx, name=name, timeout=120, stream=False)
            return r.exit_code, r.log_path.read_text(errors='replace')
        except FileNotFoundError:
            return 127, ''
    src_mod.copy_local(root, baseline_dir)
    pf['environment'] = preflight.environment(ctx, run)
    pf['baseCommit'] = gitops.init_snapshot_repo(ctx, baseline_dir)  # only to diff the repair (baseline-repair.patch)
    mvn = lambda proj, goals, name: mavenops.mvn(ctx, proj, goals, name)
    before = preflight.model_check(mvn, baseline_dir, 'baseline-model-check')
    pf['modelCheck'] = {'before': before, 'after': before}
    log(f"Maven model: {before['status']}")
    if before['status'] == 'INVALID':
        pf['repairs'] = preflight.repair(baseline_dir, report['modules'], pf['model'])
        for r in pf['repairs']:
            log(f"Reparación de baseline [MAVEN-CAMEL-MISSING-VERSION]: {r['file']}: {r['change']}")
        if pf['repairs']:
            after = preflight.model_check(mvn, baseline_dir, 'baseline-model-recheck')
            pf['modelCheck']['after'] = after
            log(f"Maven model tras reparación: {after['status']}")
            pf['repairPatch'] = gitops._git(ctx, ['diff', '--no-color'], baseline_dir, 'git-baseline-repair-diff')
        reason = None if pf['modelCheck']['after']['status'] == 'VALID' else (
            'BASELINE_POM_STILL_INVALID' if pf['repairs'] else 'BASELINE_POM_UNREPAIRABLE')
        plat = _platform(report, pf)
        if reason and plat and plat['blocked']:  # doc 42: a vendor platform BOM, not just "the POM is still invalid"
            pf['build'] = {'state': 'BASELINE_BLOCKED_PLATFORM_BOM', 'reason': plat['primaryReason'], 'errors': pf['modelCheck']['after']['errors'],
                           'stages': {'validate': 'FAIL', 'compile': 'NOT_EXECUTABLE', 'test': 'NOT_RUN'}, 'buildState': 'BUILD_NOT_EXECUTABLE'}
            log(f"Baseline bloqueado: BASELINE_BLOCKED_PLATFORM_BOM ({plat['platform']}; {plat['primaryReason']}; "
                f"reparación {plat['repair']['status']}): el análisis estático continúa")
            return report
        if reason:
            pf['build'] = {'state': 'BASELINE_BLOCKED', 'reason': reason, 'errors': pf['modelCheck']['after']['errors']}
            log(f'Baseline bloqueado: {reason}')
            return report
    elif before['status'] == 'UNKNOWN':
        pf['build'] = {'state': 'BASELINE_BLOCKED', 'reason': 'MAVEN_UNAVAILABLE', 'errors': before['errors']}
        return report
    if 'platform' not in pf:
        _platform(report, pf)
    res, eff = mavenops.effective_pom(ctx, baseline_dir)  # analysis data from the (repaired) valid model
    if res.exit_code == 0 and eff.exists():
        report = apply_effective(report, mavenops.parse_effective(eff), catalog)
        report['mavenResolution'] = 'maven-effective' + (' (tras reparación de baseline)' if pf['repairs'] else '')
    else:
        report['mavenResolution'] = 'failed'
    if not build:
        pf['build'] = {'state': 'NOT_RUN', 'reason': 'build no solicitado'}
        return report
    comp, ctext = run_validation(ctx, baseline_dir, 'baseline-compile', goals=('clean', 'compile'))
    errors = preflight.enrich(preflight.classify(ctext), 'BASELINE')
    from .baseline import dependency_resolution_classifier as drc
    groups = {m['groupId'] for m in report['modules'] if m.get('groupId')}
    issues = drc.classify(ctext, internal=set(_WORKSPACE['internal']) | {f"{m['groupId']}:{m['artifactId']}" for m in report['modules']},
                          org_prefixes={'.'.join(g.split('.')[:2]) for g in groups if g.count('.') >= 1}) if comp['status'] != 'PASS' else []
    # doc 32 §18: Maven never reached javac -> the build was NOT_EXECUTABLE, it did not FAIL
    stages = {'validate': 'PASS', 'compile': 'NOT_EXECUTABLE' if issues else comp['status'], 'test': 'NOT_RUN'}
    test = None
    has_tests = 'test' in preflight.baseline_goals(baseline_dir)
    issue_stage = 'compile' if issues else None
    if comp['status'] == 'PASS' and has_tests:
        test, ttext = run_validation(ctx, baseline_dir, 'baseline-test', goals=('test',))
        errors += preflight.enrich(preflight.classify(ttext), 'BASELINE')
        stages['test'] = test['status']
        if test['status'] != 'PASS':  # runtime/test-scope artifacts are only resolved here (e.g. a JDBC driver in runtime scope)
            t_issues = drc.classify(ttext, internal=set(_WORKSPACE['internal']) | {f"{m['groupId']}:{m['artifactId']}" for m in report['modules']},
                                    org_prefixes={'.'.join(g.split('.')[:2]) for g in groups if g.count('.') >= 1})
            if t_issues:
                issues, issue_stage, stages['test'] = issues + t_issues, 'test', 'NOT_EXECUTABLE'
    ok = comp['status'] == 'PASS' and (test is None or test['status'] == 'PASS')
    last = test or comp
    st = preflight.state(ok, pf['repairs'], errors, issues)
    pf['build'] = last | {'state': st, 'stages': stages, 'errors': errors, 'dependencyIssues': issues, 'dependencyIssueStage': issue_stage,
                          'buildState': drc.BUILD_STATE.get(st, 'BUILD_SUCCESS' if ok else ('BUILD_FAILED_TEST' if stages['test'] == 'FAIL' else 'BUILD_FAILED_CODE')),
                          'command': 'mvn -B -e -Dstyle.color=never clean compile' + (' && mvn test' if has_tests else ''),
                          'reason': None if ok else (issues[0]['reason'] if issues else baseline_reason(errors, comp['status']))}
    log(f"Baseline: {pf['build']['state']} (compile {stages['compile']}, test {stages['test']}) {last.get('tests') or ''}")
    return report


# --- plan ---------------------------------------------------------------------------------------
def build_plan(report, target: str, java: str, accepted: dict):
    """accepted: rule id -> reason (explicit human decision, recorded in the reports)."""
    from .models import Finding, Module, Route, Scan, TargetProfile
    from .planning import build_steps, evaluate_steps
    from .profiles import PROFILES, profile_digest
    p = next(x for x in PROFILES if x['runtime'] == target and x['status'] == 'validated')
    profile = TargetProfile(**p, digest=profile_digest(p))
    scan = Scan(commit_sha=None, snapshot_hash=report['snapshotHash'], errors=report['errors'], route_builders=report.get('routeBuilders', []),
                maven_resolution=report['mavenResolution'], components=report['components'],
                summary={'usage': report.get('usage', {}), 'usageEvidence': report.get('usageEvidence', {}), 'testClasses': report.get('testClasses', 0),
                         'platform': report.get('platform')})
    endpoints = [{'component': e['component'], 'file': r['file'], 'line': e['line'], 'dynamic': e['dynamic']} for r in report['routes'] for e in r['endpoints']]
    modules = [Module(path=m['file'], group_id=m['groupId'], artifact_id=m['artifactId'], packaging=m['packaging'],
                      dependencies=m['dependencies'], resolution=m['resolution']) for m in report['modules']]
    routes = [Route(file=r['file'], dsl=r['dsl'], line=r['line'], route_id=r['routeId']) for r in report['routes']]
    findings = []
    for f in report['findings']:
        findings.append(Finding(id=uuid.uuid4(), rule_id=f['ruleId'], blocking=f['blocking'], file=f['file'], line=f['line'], evidence=f.get('evidence'),
                                status='resolved' if f['ruleId'] in accepted else 'open'))
    steps, unmapped = build_steps(scan, profile, modules, routes, findings, java_version=java, endpoints=endpoints)
    from .planning import compat_blocks
    blocks = compat_blocks(steps, modules)
    for b in blocks:  # doc 19: explicit blocking finding; accepted only with --accept CAMEL_COMPONENT_COMPAT="motivo"
        rf = {'ruleId': 'CAMEL_COMPONENT_COMPAT', 'file': b['file'], 'line': b['line'], 'severity': 'high', 'classification': 'MANUAL',
              'blocking': True, 'evidence': b['evidence'], 'migrationAction': 'manual-review', 'recommendation': f"{b['code']}: {b['reason']}"}
        report['findings'].append(rf)
        findings.append(Finding(id=uuid.uuid4(), rule_id=rf['ruleId'], blocking=True, file=rf['file'], line=rf['line'], evidence=rf['evidence'],
                                status='resolved' if 'CAMEL_COMPONENT_COMPAT' in accepted else 'open'))
    if blocks:
        steps, unmapped = build_steps(scan, profile, modules, routes, findings, java_version=java, endpoints=endpoints)
    status = {str(f.id): (f.status, f.blocking) for f in findings}
    for mod, d in unmapped:  # unsupported dependency -> explicit blocking manual action
        fid = f'unmapped:{d["artifactId"]}'
        status[fid] = ('open', True)
        for s in steps:
            if s['key'].startswith('runtime-'):
                s['finding_ids'] = s['finding_ids'] + [fid]
        report['findings'].append({'ruleId': 'UNMAPPED_COMPONENT', 'file': mod.path, 'line': d['line'], 'severity': 'high',
                                   'classification': 'MANUAL', 'blocking': True, 'evidence': f"{d['groupId']}:{d['artifactId']}",
                                   'migrationAction': 'manual-review',
                                   'recommendation': 'Dependencia Camel sin equivalente verificado en el perfil: elegir reemplazo con evidencia.'})
    evaluated = evaluate_steps(steps, status)
    by_id = {str(f.id): rf for f, rf in zip(findings, report['findings'])}
    for s in evaluated:
        s['action'] = ('BLOCKED' if s['state'] == 'blocked' else 'MANUAL' if s['kind'] == 'manual'
                       else ACTION.get(s['classification'], 'MANUAL'))
        s['findings'] = [by_id[i] for i in s['findingIds'] if i in by_id]
    return {'profile': {k: p[k] for k in ('key', 'version', 'name', 'java_version', 'camel_version', 'runtime_version',
                                          'bom_coordinates', 'tooling')} | {'targetJava': java},
            'steps': evaluated, 'accepted': accepted}


# --- validate & autofix -------------------------------------------------------------------------
def run_validation(ctx, project: Path, name: str, goals=('clean', 'test')):
    from .worker import mavenops
    res = mavenops.mvn(ctx, project, list(goals), name)
    text = res.log_path.read_text(errors='replace')
    return {'name': name, 'exitCode': res.exit_code, 'status': 'PASS' if res.exit_code == 0 else 'FAIL',
            'tests': mavenops.surefire_summary(project), 'log': str(res.log_path), 'durationMs': res.duration_ms}, text


def known_fixes(log_text: str, profile_mapping: dict) -> list[dict]:
    """Low-risk fixes for recognised errors only. Anything else becomes a manual action."""
    from .profiles import CORE_COMPONENTS
    fixes = []
    fp = lambda *parts: __import__('hashlib').sha1('|'.join(parts).encode()).hexdigest()[:12]
    for pkg in sorted(set(re.findall(r'package (javax\.[\w.]+) does not exist', log_text))):
        api = next((k for k in JAKARTA_FIX if pkg == k or pkg.startswith(k + '.')), None)
        if api and any(f['fingerprint'] == fp('jakarta', JAKARTA_FIX[api]) for f in fixes):
            continue
        if api:
            fixes.append({'kind': 'jakarta-recipe', 'trigger': f'package {pkg} does not exist', 'recipe': JAKARTA_FIX[api],
                          'fingerprint': fp('jakarta', JAKARTA_FIX[api]), 'category': 'MIGRATION'})
    comps = set(re.findall(r'No endpoint could be found for: ([\w+-]+)://', log_text))
    comps |= set(re.findall(r'No component found with scheme: ([\w+-]+)', log_text))
    for c in sorted(comps):
        if c in CORE_COMPONENTS or c in {'jms', 'jdbc', 'sql', 'http', 'ftp', 'mail', 'quartz', 'jackson', 'jaxb', 'kafka'}:
            fixes.append({'kind': 'add-component', 'trigger': f'componente {c} ausente', 'component': c,
                          'artifact': profile_mapping['coreComponent'].format(c), 'fingerprint': fp('component', c), 'category': 'DEPENDENCY'})
    return fixes


def apply_fix(ctx, project: Path, fix: dict, profile, n: int):
    from .planning import recipe_yaml
    from .worker import mavenops
    tooling, m = profile['tooling'], profile['dependency_mapping']
    cfg = ctx.workdir / 'meta' / f'autofix-{n}.yml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if fix['kind'] == 'jakarta-recipe':
        cfg.write_text('---\n')
        active = [fix['recipe']]
    else:
        name = f'mf.autofix.Add{n}'
        cfg.write_text(recipe_yaml(name, f"Autofix {fix['component']}", [('org.openrewrite.maven.AddDependency', {
            'groupId': m['groupId'], 'artifactId': fix['artifact'], 'version': m['version'], 'onlyIfUsing': 'org.apache.camel..*'})]))
        active = [name]
    res = mavenops.rewrite(ctx, project, 'run', plugin_version=tooling['pluginVersion'], artifacts=tooling['artifacts'],
                           active=active, config=cfg, name=f'autofix-{n}')
    return res.exit_code == 0


# --- reports ------------------------------------------------------------------------------------
def _md_table(rows, headers):
    esc = lambda v: str('' if v is None else v).replace('|', '\\|').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)] + ['| ' + ' | '.join(esc(c) for c in r) + ' |' for r in rows])


def plan_markdown(report, plan):
    p = plan['profile']
    out = [f"# Plan de migración\n\nPerfil: **{p['name']}** (v{p['version']}) · Java destino {p['targetJava']} · Camel {p['camel_version']}",
           f"BOM: {', '.join(p['bom_coordinates'])} · rewrite-maven-plugin {p['tooling']['pluginVersion']} · recetas {', '.join(p['tooling']['artifacts'])}",
           f"\nSnapshot analizado: `{report['snapshotHash']}` · raíz Maven `{report['projectRoot']}` · resolución Maven: {report['mavenResolution']}",
           '\nEste plan se emite **antes** de modificar nada. No contiene porcentajes de compatibilidad: solo acciones clasificadas.\n',
           _md_table([(s['ordinal'], s['key'], s['action'], s['state'], ', '.join((s['recipe'] or {}).get('activeRecipes', [])) or '—',
                       '; '.join(s['blockedReasons']) or '—') for s in plan['steps']],
                     ['#', 'Paso', 'Acción', 'Estado', 'Recetas', 'Bloqueos'])]
    if plan['accepted']:
        out.append('\n## Decisiones explícitas (--accept)\n' + '\n'.join(f'- `{r}`: {why}' for r, why in plan['accepted'].items()))
    for s in plan['steps']:
        if s['notes']:
            out.append(f"\n### {s['key']}\n" + '\n'.join(f'- {n}' for n in s['notes']))
    return '\n'.join(out) + '\n'


def manual_actions(report, plan):
    items, n = [], 0
    seen = set()
    transformed = any(s['key'] == 'endpoints-config' and s['state'] == 'ready' for s in plan['steps'])
    runtime_ready = any(s['key'].startswith('runtime-') and s['state'] == 'ready' for s in plan['steps'])
    camel_ready = any(s['key'] == 'camel-3-to-4' and s['state'] == 'ready' for s in plan['steps'])
    for s in plan['steps']:
        if s['kind'] != 'manual' and s['state'] != 'blocked':
            continue
        for f in s.get('findings', []):
            key = (f['ruleId'], f['file'], f['line'])
            if key in seen or f['ruleId'] in plan['accepted'] or (transformed and f['ruleId'] in ('HTTP4', 'QUARTZ2')) \
                    or (runtime_ready and f['ruleId'] in ('PACKAGING_WAR', 'JAVA_LEGACY')) \
                    or (camel_ready and f['ruleId'] == 'CAMEL_CDI' and f['file'].endswith('pom.xml')):
                continue  # accepted by a human, or handled by the located-edit step (shown in the plan/report)
            seen.add(key)
            n += 1
            items.append({'id': f'MANUAL-{n:03d}', 'step': s['key'], 'rule': f['ruleId'], 'source': f"{f['file']}:{f['line']}",
                          'detected': f.get('evidence', ''), 'reason': f['recommendation'],
                          'risk': 'CRITICAL' if f['blocking'] else f['severity'].upper(), 'blocking': f['blocking']})
    for f in report['findings']:
        if f['ruleId'] == 'HARDCODED_SECRET' and (f['ruleId'], f['file'], f['line']) not in seen:
            seen.add((f['ruleId'], f['file'], f['line']))
            n += 1
            items.append({'id': f'MANUAL-{n:03d}', 'step': '-', 'rule': f['ruleId'], 'source': f"{f['file']}:{f['line']}",
                          'detected': f['evidence'], 'reason': f['recommendation'], 'risk': 'CRITICAL', 'blocking': False})
    for e in report['errors']:
        if e['code'] == 'SENSITIVE_FILE_EXCLUDED':
            n += 1
            items.append({'id': f'MANUAL-{n:03d}', 'step': '-', 'rule': 'SENSITIVE_FILE', 'source': e['file'], 'detected': 'archivo sensible',
                          'reason': 'No se copia al proyecto migrado (política de seguridad). Provéalo en despliegue desde un gestor de secretos.',
                          'risk': 'HIGH', 'blocking': False})
    for i in items:
        if i['rule'] == 'HARDCODED_SECRET' and transformed and i['source'].rsplit(':', 1)[0].endswith(('.properties', '.yml', '.yaml')):
            i['reason'] += ' El valor se reemplazó por ${MF_SECRET_<CLAVE>} en el proyecto migrado: defina esa variable y rote el secreto.'
    return items


def manual_markdown(items):
    if not items:
        return '# Acciones manuales\n\nNinguna.\n'
    return '# Acciones manuales\n\n' + '\n\n'.join(
        f"## {i['id']} - {i['rule']}\n\nSource:\n{i['source']}\n\nDetected:\n{i['detected'] or '—'}\n\nReason:\n{i['reason']}\n\n"
        f"Required:\nResolver y documentar la decisión; el paso `{i['step']}` no se aplica automáticamente.\n\nRisk:\n{i['risk']}"
        for i in items) + '\n'


# --- migrate ------------------------------------------------------------------------------------
RECIPE_STATES = ('APPLIED', 'SKIPPED', 'ROLLED_BACK', 'BLOCKED_BY_BASELINE', 'FAILED', 'PENDING_APPROVAL')


def migrate(source, out, target, java, accepted, *, dry_run, autofix, max_rounds, baseline, log, allow_baseline_failure=False,
            auto_up_to='REVIEW', approved=(), on_candidate=None, baseline_policy=None):
    """Doc 15 pipeline. Trees: source (read only) -> .work/baseline (repair + validation) -> .work/candidate (migration)."""
    from . import preflight, transform
    from .profiles import PROFILES
    from .security import redact, secret_files
    from .worker import gitops, mavenops, runner, source as src_mod
    if out.exists() and any(x.name != 'analysis' for x in out.iterdir()):  # analysis/: written by the workspace analysis waves
        raise InvalidInput(f'El directorio de salida no está vacío: {out}')
    if out.resolve() == source.resolve() or source.resolve() in out.resolve().parents:
        raise InvalidInput('La salida debe estar fuera del proyecto fuente')
    (out / 'reports').mkdir(parents=True)
    ctx = engine_context(out, 4)
    baseline_dir, work = out / '.work' / 'baseline', out / '.work' / 'candidate'
    report = analyze(source, target, out, True, log, work=baseline_dir, build=baseline and not dry_run)
    if on_candidate:  # workspace: the candidate resolves internal dependencies from their migrated versions
        on_candidate()
    pf = report['preflight']
    from .baseline import dependency_resolution_classifier as drc, vendor_bom_analyzer as vba
    issues = pf['build'].get('dependencyIssues') or []
    decision = drc.decide(pf['build'].get('state') or 'NOT_RUN', issues, baseline_policy)  # doc 32: BaselinePolicyEngine
    (out / 'reports' / 'baseline-report.md').write_text(redact(preflight.report_markdown(pf['environment'], pf['model'], pf['repairs'], pf['build'],
                                                                                          pf.get('modelCheck'))
                                                               + drc.report_section(str(source.name), pf['build'].get('state'), issues, decision)
                                                               + vba.report_section(pf.get('platform'), report['overview']['camel'])))
    if pf.get('platform'):  # doc 42 §9: what each dependency's version depends on, before migrating
        write_json(out / 'reports' / 'dependency-management-snapshot.json', pf['platform']['snapshot'])
    if pf.get('repairPatch'):
        (out / 'reports' / 'baseline-repair.patch').write_text(redact(pf['repairPatch']))
    state = pf['build'].get('state')
    plan = build_plan(report, target, java, accepted) if state != 'BASELINE_BLOCKED' else {'steps': [], 'accepted': accepted}
    write_json(out / 'reports' / 'analysis.json', report)
    from . import ENGINE_VERSION, rules as mrules
    from datetime import datetime, timezone
    result = {'trace': {'engineVersion': ENGINE_VERSION, 'rulesCatalog': report['catalogVersion'], 'rulesCatalogDigest': report['catalogDigest'],
                        'migrationRules': mrules.load()['version'], 'migrationRulesDigest': mrules.load()['digest'],
                        'sourceSnapshot': report['snapshotHash'], 'sourceCommit': _git_head(source), 'target': target, 'java': java,
                        'user': os.environ.get('USER') or os.environ.get('USERNAME') or 'desconocido',
                        'date': datetime.now(timezone.utc).isoformat(timespec='seconds')},
              'dryRun': dry_run, 'steps': [], 'validations': [], 'autofix': [], 'manualActions': 0, 'baseline': state,
              'baselineReason': pf['build'].get('reason'), 'baselineRepairs': pf['repairs'],
              'mavenModel': {k: v.get('status') for k, v in pf.get('modelCheck', {}).items()}, 'baselineStages': pf['build'].get('stages'),
              'baselineBuildState': pf['build'].get('buildState'), 'baselineDependencies': issues, 'baselineDecision': decision,
              'migrationMode': 'DEGRADED' if decision['migration'] == 'DEGRADED' else 'NORMAL'}
    plat = pf.get('platform')
    if plat:
        result['platform'] = {k: plat.get(k) for k in ('platform', 'signals', 'vendorBoms', 'repair', 'continuation', 'primaryReason', 'reasonCodes',
                                                        'requiredAction', 'runtimeMigration', 'repositories')}
    if issues:
        result['rootCause'] = {'primaryReason': state, 'rootCause': issues[0]['reason'], 'artifact': issues[0]['artifact'],
                               'recommendedAction': issues[0].get('hint') or 'proveer el artefacto o el repositorio aprobado'}
    model_valid = pf.get('modelCheck', {}).get('after', {}).get('status') == 'VALID'
    code_failure = state in ('BASELINE_FAILED_CODE', 'BASELINE_FAILED_TEST') and not allow_baseline_failure
    if state in ('BASELINE_BLOCKED', 'BASELINE_UNKNOWN') or not model_valid or code_failure or decision['migration'] == 'BLOCKED':
        reason = 'VENDOR_BOM_UNRESOLVED' if state == 'BASELINE_BLOCKED_PLATFORM_BOM' else 'MAVEN_MODEL_INVALID' if not model_valid else (decision.get('code') if decision['migration'] == 'BLOCKED'
                                                                 else pf['build'].get('reason') or state)
        # recipes never run on an invalid model: they are BLOCKED_BY_BASELINE, not FAILED
        result['steps'] = [{'key': s['key'], 'state': 'BLOCKED_BY_BASELINE', 'reason': reason} for s in plan['steps'] if s['kind'] in ('recipe', 'transform')] \
            or [{'key': 'recipes', 'state': 'BLOCKED_BY_BASELINE', 'reason': reason}]
        log(f'Baseline {state} ({reason}): no se ejecutan recetas (ver reports/baseline-report.md'
            + ('; use --allow-baseline-failure para continuar con fallos de código conocidos)' if code_failure else
               f"; {decision['reason']})" if decision['migration'] == 'BLOCKED' else ')'))
        primary = ('BASELINE_MAVEN_MODEL_INVALID' if not model_valid else
                   decision['code'] if decision['migration'] == 'BLOCKED' else (pf['build'].get('reason') or state))
        codes = [primary, reason] + ([state] if issues else [])
        if state == 'BASELINE_BLOCKED_PLATFORM_BOM':  # doc 42 §15: the plan exists even though its automatic execution is blocked
            primary, codes = plat['primaryReason'], plat['reasonCodes'] + [state, 'BASELINE_MAVEN_MODEL_INVALID']
            (out / 'reports' / 'migration-plan.md').write_text(plan_markdown(report, plan))
            write_json(out / 'reports' / 'migration-plan.json', plan)
            result['migrationPlan'] = 'AVAILABLE'
            result['baselineStages'] = pf['build'].get('stages')
        result.update(status='BLOCKED', primaryReason=primary, reasonCodes=list(dict.fromkeys(codes)),
                      detectedIssues=[e['message'] for e in pf['build'].get('errors', [])[:10]])
        if issues:
            result['rootCause'] |= {'secondary': result['reasonCodes'][1:], 'migration': decision['migration'], 'detail': decision.get('reason')}
        _finish(out, report, plan, result, log)
        return EXIT_ERROR
    profile = next(x for x in PROFILES if x['runtime'] == target and x['status'] == 'validated')
    (out / 'reports' / 'migration-plan.md').write_text(plan_markdown(report, plan))
    write_json(out / 'reports' / 'migration-plan.json', plan)
    actions = manual_actions(report, plan)
    result['manualActions'] = len(actions)
    (out / 'reports' / 'manual-actions.md').write_text(manual_markdown(actions))
    from . import camel_compat
    decisions = next(((s['recipe'] or {}).get('compat', []) for s in plan['steps'] if s['key'] == 'camel-3-to-4'), [])
    if decisions:
        (out / 'reports' / 'camel-component-migration-report.md').write_text(camel_compat.report_markdown(decisions))
    result['camelComponents'] = [{k: d.get(k) for k in ('artifact', 'status', 'action', 'target', 'code', 'confidence')} for d in decisions]
    if pf['build'].get('command'):
        result['validations'] += [{'target': 'baseline', 'stage': 'BASELINE', 'name': n, 'status': st}
                                  for n, st in (pf['build'].get('stages') or {}).items()]
    degraded = decision['migration'] == 'DEGRADED'
    tests_only = degraded and pf['build'].get('dependencyIssueStage') == 'test'  # compile resolves: only the tests cannot run
    if degraded:  # doc 32 §13: stubs of the missing low-criticality artifacts, only so OpenRewrite can parse and run the recipes
        from .baseline.stubs import install_stubs
        head = mavenops.current_overlay()
        if head is None:
            head = out / '.m2-degraded'
            mavenops.use_local_overlay(head)
            result['_ownOverlay'] = True
        result['degradedStubs'] = install_stubs(head, [i['artifact'] for i in issues])
        log(f"Modo DEGRADADO: {decision['reason']}; stubs solo para las recetas: {', '.join(result['degradedStubs'])}")
    # CREATE CANDIDATE FROM REPAIRED BASELINE (no .git, no target)
    src_mod.copy_local(baseline_dir, work)
    base = gitops.init_snapshot_repo(ctx, work)
    validate_model = lambda name: preflight.model_check(lambda p, g, n: mavenops.mvn(ctx, p, g, n), work, name)
    cm = validate_model('candidate-maven-model')
    result['validations'].append({'target': 'candidate', 'stage': 'CANDIDATE', 'name': 'maven-model',
                                  'status': 'PASS' if cm['status'] == 'VALID' else 'FAIL', 'errors': cm['errors']})
    log(f"candidate/maven-model: {'PASS' if cm['status'] == 'VALID' else 'FAIL'}")
    if cm['status'] != 'VALID':
        result['steps'] += [{'key': s['key'], 'state': 'BLOCKED_BY_BASELINE', 'reason': 'CANDIDATE_MAVEN_MODEL_INVALID'}
                            for s in plan['steps'] if s['kind'] in ('recipe', 'transform')]
        result.update(status='BLOCKED', primaryReason='CANDIDATE_MAVEN_MODEL_INVALID', reasonCodes=['CANDIDATE_MAVEN_MODEL_INVALID'],
                      detectedIssues=[e['message'] for e in cm['errors'][:10]])
        _finish(out, report, plan, result, log)
        return EXIT_ERROR
    tooling = profile['tooling']
    steps = [s for s in plan['steps'] if s['kind'] in ('recipe', 'transform')]
    runnable = [s for s in steps if s['state'] == 'ready']
    for s in steps:
        if s['state'] != 'ready':
            result['steps'].append({'key': s['key'], 'state': 'SKIPPED', 'reason': '; '.join(s['blockedReasons']) or s['state']})
    if dry_run:
        cfg = out / 'meta' / 'rewrite.yml'
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(''.join((s['recipe'] or {}).get('yaml', '') for s in runnable) or '---\n')
        code, patch = 0, ''
        active = [r for s in runnable for r in (s['recipe'] or {}).get('activeRecipes', [])]
        if active:
            res = mavenops.rewrite(ctx, work, 'dryRun', plugin_version=tooling['pluginVersion'], artifacts=tooling['artifacts'],
                                   active=active, config=cfg, name='rewrite-dryRun')
            code, patch = res.exit_code, mavenops.collect_patches(work)
        for s in runnable:
            if (s['recipe'] or {}).get('edits'):
                transform.apply_edits(work, s['recipe']['edits'], target)
        gitops._git(ctx, ['add', '-A'], work, 'git-add-preview')
        patch += gitops._git(ctx, ['diff', '--cached', '--no-color'], work, 'git-diff-preview')
        (out / 'reports' / 'dry-run.patch').write_text(redact(patch))
        result['steps'] += [{'key': s['key'], 'state': 'APPLIED' if code == 0 else 'FAILED', 'preview': True} for s in runnable]
        result['status'] = 'PREVIEW'
        _finish(out, report, plan, result, log)
        return EXIT_OK if code == 0 else EXIT_ERROR
    secrets_before = secret_files(work)
    failed = set()
    patches, snapshots, fix_steps = {}, {}, []
    from . import ledger, rules as mrules
    from .pom import normalizer

    def record_fix(f, prev, commit):  # autofix commits are changes too: they get a ledger entry
        if commit:
            key = f"autofix-{len(fix_steps) + 1}"
            fix_steps.append({'key': key, 'kind': 'autofix', 'classification': 'AUTO_TEST', 'recipe': {'rules': [f.get('category') or 'AUTOFIX']}, 'findings': []})
            patches[key], snapshots[key] = gitops.full_patch(ctx, work, prev, commit), (prev, commit)
    for s in runnable:
        if mrules.needs_approval(s['classification'], auto_up_to) and s['key'] not in approved:  # improvements/05 threshold
            result['steps'].append({'key': s['key'], 'state': 'PENDING_APPROVAL',
                                    'reason': f"nivel {s['classification']} por debajo del umbral de auto-aplicación {auto_up_to}: use --approve {s['key']}"})
            failed.add(s['key'])
            continue
        if not model_valid:  # precondition of every recipe (doc 15 §8)
            result['steps'].append({'key': s['key'], 'state': 'BLOCKED_BY_BASELINE', 'reason': 'MAVEN_MODEL_INVALID'})
            continue
        if any(d in failed for d in s['dependsOn']):
            result['steps'].append({'key': s['key'], 'state': 'SKIPPED', 'reason': f"depende de un paso no aplicado: {sorted(failed & set(s['dependsOn']))}"})
            failed.add(s['key'])
            continue
        cfg = out / 'meta' / f"rewrite-{s['key']}.yml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(s['recipe'].get('yaml') or '---\n')
        prev = gitops.head(ctx, work)
        try:
            if s['recipe'].get('pomMapping'):  # doc 24 phase 2: located POM edit, no artifact resolution needed
                edits = normalizer.run_mapping_step(work, s['recipe']['pomMapping'])
                result.setdefault('pomMapping', []).extend(edits)
                res = runner.Result(0, 0, out / 'logs' / f"transform-{s['key']}.log")
                res.log_path.parent.mkdir(parents=True, exist_ok=True)
                res.log_path.write_text(json.dumps(edits, ensure_ascii=False, indent=2))
            elif s['recipe'].get('edits'):
                edits = transform.apply_edits(work, s['recipe']['edits'], target)
                result.setdefault('edits', []).extend(edits)
                res = runner.Result(0 if all(e['applied'] for e in edits) else 1, 0, out / 'logs' / f"transform-{s['key']}.log")
                res.log_path.parent.mkdir(parents=True, exist_ok=True)
                res.log_path.write_text(json.dumps(edits, ensure_ascii=False, indent=2))
            else:
                res = mavenops.rewrite(ctx, work, 'run', plugin_version=tooling['pluginVersion'], artifacts=tooling['artifacts'],
                                       active=s['recipe']['activeRecipes'], config=cfg, name=f"rewrite-{s['key']}")
        except (runner.Cancelled, runner.TimedOut):
            raise
        except Exception as e:  # tooling crash: FAILED (not a migration result)
            gitops.reset_hard(ctx, work, prev)
            failed.add(s['key'])
            result['steps'].append({'key': s['key'], 'state': 'FAILED', 'reason': f'TOOLING: {type(e).__name__}'})
            continue
        if res.exit_code != 0:
            gitops.reset_hard(ctx, work, prev)
            failed.add(s['key'])
            errs = preflight.enrich(preflight.classify(res.log_path.read_text(errors='replace')), 'CANDIDATE')
            result['steps'].append({'key': s['key'], 'state': 'ROLLED_BACK', 'log': str(res.log_path),
                                    'reason': errs[0]['category'] + ': ' + errs[0]['message'][:160] if errs else 'la receta terminó con error'})
            log(f"Paso {s['key']}: ROLLED_BACK")
            continue
        if s['kind'] == 'recipe' and (after := validate_model(f"maven-model-after-{s['key']}"))['status'] != 'VALID':
            gitops.reset_hard(ctx, work, prev)  # never keep a state Maven cannot read
            failed.add(s['key'])
            result['steps'].append({'key': s['key'], 'state': 'ROLLED_BACK',
                                    'reason': 'CANDIDATE_MAVEN_MODEL_INVALID: ' + '; '.join(e['message'][:120] for e in after['errors'][:2])})
            log(f"Paso {s['key']}: ROLLED_BACK (dejó el POM inválido)")
            continue
        norm = normalizer.normalize(work)  # doc 23: after every phase that may touch the POM
        if norm['changes'] or norm['conflicts']:
            result.setdefault('pomNormalization', []).append({'step': s['key']} | norm)
        commit = gitops.commit_all(ctx, work, f"migration-factory: {s['key']}")
        files = gitops.changed_files(ctx, work, prev, commit) if commit else []
        if commit:
            patches[s['key']], snapshots[s['key']] = gitops.full_patch(ctx, work, prev, commit), (prev, commit)
        result['steps'].append({'key': s['key'], 'state': 'APPLIED', 'files': files, 'changes': len(files)})
        log(f"Paso {s['key']}: APPLIED ({len(files)} archivos)")
    # SECRET RESCAN (findings != tool errors)
    try:
        remaining = secret_files(work)
        externalized = [e for e in result.get('edits', []) if e['op'] in ('secret', 'uri-secret') and e['applied']]
        result['security'] = {'secretsDetected': len(secrets_before) + 0, 'secretsExternalized': len(externalized),
                              'remainingFiles': remaining,
                              'status': 'PASS' if not remaining else 'FINDINGS',
                              'initialStatus': 'WARN' if secrets_before else 'PASS'}
    except Exception as e:  # scanner failure is ERROR, never reported as a finding
        result['security'] = {'status': 'ERROR', 'reason': f'SCANNER_EXECUTION_FAILED: {type(e).__name__}'}
    result['secretScan'] = result['security']['status']
    # CANDIDATE VALIDATE -> COMPILE (+AUTOFIX) -> TEST
    known = {e['fingerprint'] for e in pf['build'].get('errors', [])}

    def tag(errors):
        return [e | {'origin': 'BASELINE' if e['fingerprint'] in known else 'MIGRATION',
                     'category': e['category'] if e['fingerprint'] in known or e['category'] in ('ENVIRONMENT', 'TEST', 'TOOLING', 'BUILD_MODEL', 'DEPENDENCY')
                     else 'MIGRATION'} for e in preflight.enrich(errors, 'CANDIDATE')]
    mcheck = preflight.model_check(lambda p, g, n: mavenops.mvn(ctx, p, g, n), work, 'candidate-validate')
    result['validations'].append({'target': 'candidate', 'stage': 'CANDIDATE', 'name': 'validate', 'status': 'PASS' if mcheck['status'] == 'VALID' else 'FAIL',
                                  'errors': tag(mcheck['errors'])})
    # CANDIDATE PREFLIGHT GATES (docs 22/26/29): no Maven build of a candidate already known to be invalid
    from .dependency import preflight_gates
    dg = preflight_gates.run(work, tooling['camelRecipeTarget'], usage=report.get('usage', {}), decisions=decisions)
    result['preflightGates'] = dg['gates']
    if dg['rootCause']:
        result['rootCause'] = dg['rootCause']
    for g in dg['gates']:
        result['validations'].append({'target': 'candidate', 'stage': 'CANDIDATE', 'name': g['name'], 'status': g['status'],
                                      'reason': g.get('reason') or g.get('code')})
        log(f"{g['name']}: {g['status']}" + (f" ({g.get('code') or g.get('reason')})" if g['status'] in ('FAIL', 'SKIPPED') else ''))
    compile_ok = test_ok = False
    rounds = 0
    why = 'DEPENDENCY_RESOLUTION_BLOCKED: ' + ', '.join(i['artifact'] for i in issues)
    if degraded and not tests_only and dg['status'] != 'FAIL':  # doc 32 §13/§18: the build cannot be reproduced without the missing artifacts
        result['validations'] += [{'target': 'candidate', 'stage': 'CANDIDATE', 'name': n, 'status': 'NOT_EXECUTABLE', 'reason': why}
                                  for n in ('compile', 'test')]
        log(f'Candidate build: NOT_EXECUTABLE ({why})')
    if dg['status'] == 'FAIL':
        result['validations'].append({'target': 'candidate', 'stage': 'CANDIDATE', 'name': 'compile', 'status': 'SKIPPED',
                                      'reason': f"BLOCKED_BY_PREFLIGHT_GATE: {dg['primary']}"})
    from . import compile_errors
    previous_count, round_start = None, None
    while mcheck['status'] == 'VALID' and dg['status'] != 'FAIL' and (not degraded or tests_only):
        v, text = run_validation(ctx, work, f'candidate-compile-{rounds}', goals=('clean', 'compile'))
        java = compile_errors.summary(text, v['status'] != 'PASS')  # doc 28: structured javac errors per round
        result['validations'].append(v | {'target': 'candidate', 'stage': 'CANDIDATE', 'name': 'compile', 'round': rounds,
                                          'errors': tag(preflight.classify(text)), 'javaErrors': java})
        result['compileErrors'] = java
        log(f"Candidate compile (ronda {rounds}): {v['status']}" + (f" · {java['count']} error(es) Java {java['byType']}" if java['count'] else
                                                                     (f" · {java['parser']}" if v['status'] != 'PASS' else '')))
        if v['status'] == 'PASS':
            compile_ok = True
            break
        if previous_count is not None and java['count'] > previous_count and round_start:
            gitops.reset_hard(ctx, work, round_start)  # the round made it worse: back to its snapshot, stop
            for a in result['autofix']:
                if a['round'] == rounds:
                    a['rolledBack'] = True
            result['autofixStopped'] = f'AUTOFIX_ROLLED_BACK: la ronda {rounds} aumentó los errores ({previous_count} → {java["count"]})'
            log(result['autofixStopped'])
            break
        if not autofix or rounds >= max_rounds:
            break
        done = {a['fix']['fingerprint'] for a in result['autofix']}
        fixes = [f for f in known_fixes(text, profile['dependency_mapping']) if f['fingerprint'] not in done]  # never repeat a fix
        if not fixes:
            result['autofixStopped'] = ('NO_KNOWN_FIX: ' + (f"{java['count']} error(es) Java clasificados {java['byType']} sin corrección conocida"
                                                            if java['count'] else java['parser']))
            log(f"Sin correcciones conocidas para los errores restantes ({result['autofixStopped']}): quedan como acción manual")
            break
        rounds += 1
        previous_count, round_start = java['count'], gitops.head(ctx, work)  # snapshot per round
        for f in fixes:
            prev = gitops.head(ctx, work)
            ok = apply_fix(ctx, work, f, profile, len(result['autofix']) + 1)
            commit = gitops.commit_all(ctx, work, f"migration-factory autofix: {f['trigger']}") if ok else None
            if not ok:
                gitops.reset_hard(ctx, work, prev)
            result['autofix'].append({'round': rounds, 'fix': f, 'applied': bool(commit)})
            record_fix(f, prev, commit)
            log(f"Autofix ronda {rounds}: {f['trigger']} -> {'aplicado' if commit else 'no aplicado'}")
    if compile_ok and tests_only:  # the compiler ran (real validation); the tests need the missing runtime artifacts
        result['validations'].append({'target': 'candidate', 'stage': 'CANDIDATE', 'name': 'test', 'status': 'NOT_EXECUTABLE', 'reason': why})
        log(f'Candidate test: NOT_EXECUTABLE ({why})')
    elif compile_ok:
        v, text = run_validation(ctx, work, 'candidate-test', goals=('test',))
        errs = tag(preflight.classify(text))
        if v['status'] != 'PASS' and autofix:  # runtime-only gaps (missing component) surface in tests
            fixes = [f for f in known_fixes(text, profile['dependency_mapping'])
                     if f['fingerprint'] not in {a['fix']['fingerprint'] for a in result['autofix']}]
            for f in fixes[:max(0, max_rounds - rounds)]:
                prev = gitops.head(ctx, work)
                ok = apply_fix(ctx, work, f, profile, len(result['autofix']) + 1)
                commit = gitops.commit_all(ctx, work, f"migration-factory autofix: {f['trigger']}") if ok else None
                if not ok:
                    gitops.reset_hard(ctx, work, prev)
                result['autofix'].append({'round': rounds + 1, 'fix': f, 'applied': bool(commit)})
                record_fix(f, prev, commit)
            if fixes:
                result['validations'].append(v | {'target': 'candidate', 'stage': 'CANDIDATE', 'name': 'test', 'errors': errs})
                v, text = run_validation(ctx, work, 'candidate-test-retry', goals=('test',))
                errs = tag(preflight.classify(text))
        result['validations'].append(v | {'target': 'candidate', 'stage': 'CANDIDATE', 'name': 'test', 'errors': errs})
        test_ok = v['status'] == 'PASS'
        log(f"Candidate test: {v['status']} {v.get('tests') or ''}")
    if not compile_ok and not degraded:
        result['validations'].append({'target': 'candidate', 'stage': 'CANDIDATE', 'name': 'test', 'status': 'SKIPPED',
                                      'reason': 'SKIPPED_DUE_TO_COMPILE_FAILURE'})
    entries = ledger.build(result['steps'] + [{'key': x['key'], 'state': 'APPLIED'} for x in fix_steps], {'steps': plan['steps'] + fix_steps},
                           patches, ledger.project_factors(report), snapshots)
    for e in entries:
        (out / 'reports' / e['patch']).parent.mkdir(parents=True, exist_ok=True)
        (out / 'reports' / e['patch']).write_text(redact(patches[e['step']]))
    write_json(out / 'reports' / 'changes.json', entries)
    (out / 'reports' / 'changes.md').write_text(ledger.markdown(entries))
    tip = gitops.head(ctx, work)
    patch = gitops.full_patch(ctx, work, base, tip) if tip != base else ''
    (out / 'reports' / 'candidate.patch').write_text(redact(patch))  # migration only; baseline repair is in baseline-repair.patch
    result['filesChanged'] = [{'path': p, 'change': k, 'additions': a, 'deletions': d} for p, k, a, d, _ in gitops.diff_files(ctx, work, base, tip)] if patch else []
    src_mod.copy_local(work, out / 'migrated-project')  # deliverable: no .git, no target
    from . import sbom
    try:  # SBOM of the candidate as Maven resolves it; static module data if the effective POM cannot be produced
        res, eff_path = mavenops.effective_pom(ctx, work)
        eff = mavenops.parse_effective(eff_path) if res.exit_code == 0 and eff_path.exists() else None
    except (runner.Cancelled, runner.TimedOut):
        raise
    except Exception:
        eff = None
    if eff is None:
        from .analysis.scanner import scan as static_scan
        eff = {(m['groupId'], m['artifactId']): {'deps': {(d['groupId'], d['artifactId']): d['versionResolved'] for d in m['dependencies']}}
               for m in static_scan(work, target)['modules']}
    bom = sbom.cyclonedx(eff, result['trace']['engineVersion'], result['trace']['date'])
    write_json(out / 'reports' / 'sbom.cdx.json', bom)
    result['sbomComponents'] = len(bom['components'])
    from .worker.tasks import aggregate_status, build_root_causes
    kinds = {s['key']: s['kind'] for s in plan['steps']}
    smap = {'APPLIED': 'applied', 'ROLLED_BACK': 'failed-rolled-back', 'FAILED': 'failed-rolled-back',
            'BLOCKED_BY_BASELINE': 'blocked-by-baseline', 'SKIPPED': 'skipped', 'PENDING_APPROVAL': 'skipped'}
    last = {}
    for v in result['validations']:
        if v.get('target') == 'candidate':
            last[('compile' if v['name'] == 'compile' else v['name'], 'candidate')] = v['status']
    last[('secret-scan', 'candidate')] = result['secretScan']
    agg = aggregate_status([{'key': s['key'], 'kind': kinds.get(s['key'], 'recipe'), 'state': smap.get(s['state'], 'skipped')} for s in result['steps']],
                           last, plan_blocked=[s for s in plan['steps'] if s['kind'] == 'manual' and s['state'] == 'blocked'],
                           manual_actions=result['manualActions'],
                           not_executable=degraded,
                           leading=(['CAMEL_2_TO_3_DECISION_REQUIRED'] if any(s['key'] == 'camel-2-to-3' and s['state'] == 'blocked' for s in plan['steps']) else [])
                           + ([dg['primary']] if dg['status'] == 'FAIL' else [])
                           + build_root_causes(last.get(('compile', 'candidate')), last.get(('test', 'candidate')),
                                               [e for v in result['validations'] if v.get('target') == 'candidate' for e in v.get('errors') or []],
                                               (result.get('compileErrors') or {}).get('errors'))
                           + [d['code'] for d in decisions if d['action'] == 'BLOCK' and 'CAMEL_COMPONENT_COMPAT' not in accepted])
    result.update(status=agg['status'], primaryReason=agg['primaryReason'], reasonCodes=agg['reasonCodes'])
    result['rootCause'] = root_cause(result, decisions, tooling['camelRecipeTarget']) if not degraded else (result.get('rootCause') or {}) | {
        'secondary': result['reasonCodes'], 'migration': 'DEGRADED'}
    cv = {v['name']: v['status'] for v in result['validations'] if v.get('target') == 'candidate'}
    result['validationConfidence'] = drc.confidence_factors(state in ('BASELINE_SUCCESS', 'BASELINE_REPAIRED'), not issues,
                                                            {'PASS': True, 'FAIL': False}.get(cv.get('compile')), {'PASS': True, 'FAIL': False}.get(cv.get('test')))
    result['candidateBuildState'] = ('BUILD_NOT_EXECUTABLE' if degraded and not (tests_only and cv.get('compile') == 'PASS') else
                                     'BUILD_BLOCKED_DEPENDENCY' if degraded else 'BUILD_SUCCESS' if cv.get('compile') == 'PASS' and cv.get('test') == 'PASS'
                                     else 'BUILD_FAILED_TEST' if cv.get('compile') == 'PASS' else 'BUILD_FAILED_CODE' if cv.get('compile') == 'FAIL'
                                     else 'BUILD_NOT_EXECUTABLE')
    _finish(out, report, plan, result, log)
    return EXIT_OK if agg['status'] in ('SUCCEEDED', 'PARTIAL_SUCCESS') else EXIT_BUILD


def _finish(out, report, plan, result, log):
    if result.pop('_ownOverlay', False):
        from .worker import mavenops
        mavenops.use_local_overlay(None)
    vals = result['validations']
    cand = {v['name']: v for v in vals if v.get('target') == 'candidate'}
    tests = (cand.get('test') or {}).get('tests')
    summary = {
        'automaticChanges': sum(s['state'] == 'APPLIED' and _action(plan, s['key']) == 'AUTOMATIC' for s in result['steps']),
        'automaticWithReview': sum(s['state'] == 'APPLIED' and _action(plan, s['key']) == 'AUTOMATIC_WITH_REVIEW' for s in result['steps']),
        'manual': result['manualActions'], 'blocked': sum(s['state'] == 'blocked' for s in plan['steps']),
        'build': (cand.get('compile') or {}).get('status', 'NOT_RUN'), 'tests': tests,
        'autoFixes': sum(a['applied'] for a in result['autofix']),
    }
    env = report.get('preflight', {}).get('environment', [])
    warn = [c for c in env if c['status'] != 'PASS']
    summary['environment'] = 'FAIL' if any(c['status'] == 'FAIL' for c in env) else ('PASS' + (f' with {len(warn)} warning(s)' if warn else '')) if env else 'NOT_RUN'
    summary['baseline'] = result.get('baseline') or 'NOT_RUN'
    summary['baselineAutoFixes'] = len(result.get('baselineRepairs', []))
    result['summary'] = summary
    write_json(out / 'reports' / 'migration-report.json', result)
    pf = report.get('preflight', {})
    mc = result.get('mavenModel') or {}
    sec = result.get('security') or {}
    missing = [f for f in pf.get('model', []) if f.get('severity') == 'HIGH' and f.get('artifact')]
    stages = result.get('baselineStages') or {}
    t = tests or {}
    run = ['Migration Run', '=============', '', 'Baseline', '--------', f"Maven model: {mc.get('before', 'NOT_RUN')}"]
    if missing:
        run += ['', 'Detected:', f'{len(missing)} dependencias sin versión:'] + [f"- {f['artifact']}" for f in missing]
    for r in result.get('baselineRepairs', []):
        run += ['', f"[MAVEN-CAMEL-MISSING-VERSION]", 'Applied repair:', r['change'], 'Evidence:', r['basis'],
                'Confidence: HIGH (versión Camel inequívoca en el POM y BOM verificado en el repositorio)', 'AUTO_FIX: YES']
    if result.get('baselineRepairs'):
        run += ['', f"Maven model after repair: {mc.get('after', 'NOT_RUN')}"]
    run += ['', f"Baseline compile: {stages.get('compile', 'NOT_RUN')}", f"Baseline test: {stages.get('test', 'NOT_RUN')}",
            f"Baseline state: {summary['baseline']}" + (f" ({result.get('baselineReason')})" if result.get('baselineReason') else ''),
            *([f"Baseline build: {result.get('baselineBuildState')}"] if result.get('baselineBuildState') not in (None, 'BUILD_SUCCESS') else []),
            *[f"Missing dependency: {i['artifact']} ({i['reason']}, {i['dependencyCategory']}, impacto {i['migrationCriticality']})"
              for i in result.get('baselineDependencies') or []],
            *([f"Migration mode: {result.get('migrationMode')}"] if result.get('migrationMode') == 'DEGRADED' else []),
            '', 'Recipes', '-------'] + [f"{s['key']}: {s['state']}" + (f" ({s['reason']})" if s.get('reason') else '') for s in result['steps']]
    run += ['', 'Security', '--------', f"Secrets detected: {sec.get('secretsDetected', 'n/a')}",
            f"Secrets externalized: {sec.get('secretsExternalized', 'n/a')}", f"Rescan: {sec.get('status', 'NOT_RUN')}"]
    if result.get('preflightGates'):  # docs 22/26/29
        run += ['', 'Preflight gates', '---------------'] + [f"{g['name']}: {g['status']}" + (f" ({g.get('code') or g.get('reason')})"
                                                                                      if g['status'] in ('FAIL', 'SKIPPED') else '')
                                                         for g in result['preflightGates']]
    run += ['', 'Candidate', '---------', f"candidate/maven-model: {(cand.get('maven-model') or {}).get('status', 'NOT_RUN')}"]
    run += [f"mvn {n}: " + ('SUCCESS' if (cand.get(n) or {}).get('status') == 'PASS' else
                           ((cand.get(n) or {}).get('status', 'NOT_RUN') + (f" ({cand[n]['reason']})" if (cand.get(n) or {}).get('reason') else '')))
            for n in ('validate', 'compile', 'test')]
    if t:
        run.append(f"Tests: {t.get('tests', 0) - t.get('failures', 0) - t.get('errors', 0)}/{t.get('tests', 0)}")
    if result.get('validationConfidence'):
        run += [f"Candidate build state: {result.get('candidateBuildState')}", f"Validation confidence: {result['validationConfidence']['level']}"]
    run += [f"Target auto fixes: {summary['autoFixes']}", f"Manual actions: {summary['manual']}", '', 'Result', '------',
            f"Migration status: {result.get('status', 'UNKNOWN')}"]
    if result.get('primaryReason'):
        run += ['', 'Primary reason:', result['primaryReason'], 'Reason codes:'] + [f'- {c}' for c in result.get('reasonCodes', [])]
    rc = result.get('rootCause') or {}
    for label, key in (('Artifact', 'artifact'), ('Version', 'version'), ('Target', 'target'), ('File', 'file'), ('Symbol', 'symbol'),
                       ('Gate', 'gate'), ('Recommended action', 'recommendedAction')):
        if rc.get(key):
            run += ['', f'{label}:', str(rc[key])]
    if result.get('autofixStopped'):
        run += ['', 'Autofix:', result['autofixStopped']]
    if result.get('detectedIssues'):
        run += ['', 'Detected issues:'] + [f'- {m}' for m in result['detectedIssues']]
    errors = list(dict.fromkeys((e['stage'], e['category'], e.get('origin', ''), e['blocking'], e['autoFixable'], e['message'][:150],
                                 e.get('remediation') or '') for v in vals if v.get('target') == 'candidate' for e in v.get('errors', [])))
    hints = sorted({e[6] for e in errors if e[6]})
    if hints:
        run += ['', 'Remediation:'] + [f'- {h}' for h in hints]
    lines = ['# Reporte de migración', '', '```text', *run, '```', '',
             'Sin porcentaje de "readiness": compilar y pasar pruebas no demuestra equivalencia funcional (ver docs/16_EQUIVALENCIA.md).',
             'Cambios de baseline repair: reports/baseline-repair.patch · cambios de la migración: reports/candidate.patch.', '',
             '## Recetas', _md_table([(s['key'], s['state'], s.get('changes', ''), s.get('reason', '')) for s in result['steps']],
                                     ['Paso', 'Estado', 'Archivos', 'Motivo']), '',
             '## Archivos (migración)', _md_table([(f['path'], f['change'], f"+{f['additions']}/-{f['deletions']}") for f in result.get('filesChanged', [])],
                                                ['Archivo', 'Cambio', 'Líneas']) if result.get('filesChanged') else 'Ninguno.', '',
             '## Autocorrección', _md_table([(a['round'], a['fix'].get('category'), a['fix']['trigger'], a['fix']['fingerprint'], a['applied']) for a in result['autofix']],
                                            ['Ronda', 'Categoría', 'Disparador', 'Fingerprint', 'Aplicado']) if result['autofix'] else 'Ninguna.', '',
             '## Errores Java (estructurados)', _md_table([(e['file'], e['line'], e['errorType'], e.get('symbol') or '', e['confidence'])
                                                          for e in (result.get('compileErrors') or {}).get('errors', [])[:100]],
                                                         ['Archivo', 'Línea', 'Tipo', 'Símbolo', 'Confianza'])
             if (result.get('compileErrors') or {}).get('errors') else f"{(result.get('compileErrors') or {}).get('parser', 'Ninguno')}.", '',
             '## Errores del candidato', _md_table(errors, ['Etapa', 'Categoría', 'Origen', 'Bloquea', 'Autocorregible', 'Error', 'Remediación']) if errors else 'Ninguno.', '',
             f"## Inventario\nRutas XML: {report['summary']['routesXmlParsed']} · rutas Java (aprox.): {report['summary']['routesJavaApproximate']} · "
             f"integraciones: {report['summary'].get('integrationsByType')}"]
    (out / 'reports' / 'migration-report.md').write_text('\n'.join(lines) + '\n')
    est = write_effort(out / 'reports', report, len(result.get('filesChanged', [])) if 'filesChanged' in result else None)
    from . import reports, sbom
    reports.write_all(out / 'reports', report, result, est, result.get('sbomComponents'))
    for d in ('meta', 'home', 'tmp', '.work', '.migration-tmp', '.m2-degraded'):  # .m2-degraded: stubs, never delivered
        shutil.rmtree(out / d, ignore_errors=True)
    from .security import redact
    for f in (out / 'logs').glob('*.log'):  # git diff / Maven output may echo removed secret values
        f.write_text(redact(f.read_text(errors='replace')))
    _package(out, plan, result)
    (out / 'reports' / 'checksums.sha256').write_text(sbom.checksums(out))  # last: hashes of everything delivered


def _package(out: Path, plan, result):
    """Doc 19 part B: migrated-project.zip = final validated candidate + .migration/ (manifest, report, manual actions, diff)."""
    if not (out / 'migrated-project').exists():
        return  # BLOCKED: nothing migrated; the reports are the result
    from .output import result as res
    prof = plan.get('profile') or {}
    cand = {v['name']: v['status'] for v in result['validations'] if v.get('target') == 'candidate'}
    man = res.manifest(status=result.get('status'), source='local',
                       target={'java': prof.get('targetJava') or prof.get('java_version'), 'camel': prof.get('camel_version'),
                               'runtime': prof.get('name')}, projects=1, build_passed=cand.get('compile') == 'PASS',
                       tests_passed=cand.get('test') == 'PASS', manual_actions=result.get('manualActions', 0),
                       extra={'primaryReason': result.get('primaryReason'), 'trace': result.get('trace')})
    rd = lambda n: (out / 'reports' / n).read_text() if (out / 'reports' / n).exists() else ''
    res.build_zip(out / 'migrated-project', out / 'migrated-project.zip', man,
                  {'migration-report.md': rd('migration-report.md'), 'manual-actions.md': rd('manual-actions.md'), 'diff.patch': rd('candidate.patch')})


def root_cause(result: dict, decisions, target_camel: str) -> dict | None:
    """Doc 30: primary cause with its evidence (artifact, target, recommended action) and the secondary causes."""
    primary = result.get('primaryReason')
    if not primary or result.get('status') in ('SUCCEEDED',):
        return None
    secondary = [c for c in result.get('reasonCodes', []) if c != primary]
    base = {'primaryReason': primary, 'secondary': secondary}
    pre = result.get('rootCause')  # from the preflight gates
    if pre and pre.get('primaryReason') == primary:
        return pre | base
    d = next((d for d in decisions if d['action'] == 'BLOCK' and d.get('code') == primary), None)
    if d:
        action = f"REPLACE_WITH {d['target']}" if d.get('target') else (
            'ALTERNATIVAS: ' + ', '.join(a['name'] for a in d.get('alternatives', [])) if d.get('alternatives') else 'REVISIÓN MANUAL')
        return base | {'artifact': d['artifact'], 'target': f'Camel {target_camel}', 'recommendedAction': action, 'detail': d['reason']}
    java = (result.get('compileErrors') or {}).get('errors') or []
    if primary == 'JAVA_COMPILATION_ERROR' and java:
        e = java[0]
        return base | {'file': f"{e['file']}:{e['line']}", 'errorType': e['errorType'], 'symbol': e.get('symbol'),
                       'recommendedAction': 'corregir el código o la dependencia que provee el símbolo (ver compileErrors)'}
    if primary == 'CAMEL_2_TO_3_DECISION_REQUIRED':
        return base | {'recommendedAction': 'revisar la guía Camel 2→3 y registrar la decisión (--accept CAMEL2_VERSION="motivo")'}
    return base


def baseline_policy(args=None) -> dict:
    """Doc 32 §22-23: per-category policy (MF_BASELINE_* settings) refined by the specific CLI flags."""
    from .config import get_settings
    s = get_settings()
    p = {'allowExternalDependencyFailure': s.baseline_allow_external_dependency_failure, 'allowMediumCriticality': False,
         'allowNetworkFailure': s.baseline_allow_network_failure, 'allowAuthFailure': s.baseline_allow_auth_failure,
         'allowInternalArtifactFailure': s.baseline_allow_internal_artifact_failure, 'allowRepositoryFailure': s.baseline_allow_repository_failure}
    if args is not None:
        if getattr(args, 'allow_baseline_dependency_failure', False):
            p |= {'allowExternalDependencyFailure': True, 'allowMediumCriticality': True}
        if getattr(args, 'allow_baseline_network_failure', False):
            p['allowNetworkFailure'] = True
        if getattr(args, 'allow_baseline_repository_failure', False):
            p['allowRepositoryFailure'] = True
    return p


def _git_head(path: Path) -> str | None:
    """Commit of a Git working copy given as source (read only: .git/HEAD and refs), else None."""
    git = path / '.git'
    try:
        head = (git / 'HEAD').read_text().strip()
        if head.startswith('ref: '):
            ref = git / head[5:]
            if ref.is_file():
                return ref.read_text().strip()
            packed = (git / 'packed-refs').read_text() if (git / 'packed-refs').is_file() else ''
            return next((l.split()[0] for l in packed.splitlines() if l.endswith(' ' + head[5:])), None)
        return head
    except OSError:
        return None


def write_effort(reports_dir: Path, report, changed_files=None, config_path=None):
    from . import effort
    cfg = effort.load_config(config_path or os.environ.get('MF_EFFORT_CONFIG'))
    units = effort.work_units(report['findings'], report.get('integrations', []), report.get('routeBuilders', []),
                              report.get('testClasses', 0), changed_files, cfg)
    est = effort.estimate(units, cfg)
    est['complexity'] = effort.complexity_score(report)
    write_json(reports_dir / 'effort-estimate.json', est)
    (reports_dir / 'effort-estimate.md').write_text(effort.markdown(est))
    return est


def _action(plan, key):
    return next((s['action'] for s in plan['steps'] if s['key'] == key), None)


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))


# --- workspace (17-CORRECCION-MULTI-PROJECT-ROOT-AMBIGUOUS) ----------------------------------------
def discover(source: Path) -> dict:
    """Classification + projects (dirs relative to the workspace) + internal graph + order/waves."""
    from . import workspace
    info = workspace.classify_dir(source)
    if info.get('error'):
        raise InvalidInput('No se encontró ningún pom.xml: no parece un proyecto Maven')
    ws = source if info['workspaceRoot'] == '.' else source / info['workspaceRoot']
    rel = lambda d: d if info['workspaceRoot'] == '.' else d[len(info['workspaceRoot']) + 1:]
    projects = [p | {'dir': rel(p['dir'])} for p in info['projects']]
    graph = workspace.dependency_graph(ws, projects)
    return {'info': info, 'dir': ws, 'projects': projects, 'graph': graph, 'order': workspace.plan_order(graph)}


def resolve_project(source: Path, name: str) -> Path:
    d = discover(source)
    hit = next((p for p in d['projects'] if name in (p['name'], p['dir'], Path(p['dir']).name)), None)
    if hit is None:
        raise InvalidInput(f"Proyecto {name} no encontrado; proyectos: {[p['name'] for p in d['projects']]}")
    return d['dir'] if hit['dir'] == '.' else d['dir'] / hit['dir']


def analyze_workspace(source: Path, out: Path | None, effective: bool, log, discover_only=False) -> dict:
    """Doc 18/20: every project analysed by analysis waves (parallel inside a wave, MF_WORKSPACE_PARALLELISM), context
    propagated to dependents, failures isolated per project."""
    from .orchestration import migration_wave_orchestrator as mig, multi_project_orchestrator as orch
    ws = orch.build_context(source)
    log(f"{ws.info['repositoryType']}: {len(ws.projects)} proyecto(s) en {ws.info['workspaceRoot']}")
    if not discover_only:
        orch.analyze_all(ws, lambda p: analyze(p.root, 'spring', None, effective, lambda m, n=p.name: log(f'[workspace][analysis][{n}][analysis] {m}')),
                         log=log)
    if out:
        orch.write_outputs(ws, out)
        for p in ws.projects:
            if p.report is not None:
                write_effort(out / 'projects' / p.name / 'analysis', p.report)
    return {'repositoryType': ws.info['repositoryType'], 'workspaceRoot': ws.info['workspaceRoot'], 'analysisStatus': ws.analysis_status,
            'workspaceStatus': mig.workspace_status(ws.analysis_status, None) if not discover_only else None,
            **orch.counts(ws), 'projects': [p.summary() | {'dir': str(p.root.relative_to(ws.workspace_root))} for p in ws.projects],
            'analysisWaves': ws.analysis_waves, 'cycles': ws.order['cycles'], 'waves': ws.order['waves'], 'blocked': ws.order['blocked'],
            'shared': ws.shared_findings['metrics'] if ws.shared_findings else None}


def _package_workspace(out: Path, source: Path, d: dict, results: dict):
    """Doc 19 §17: migrated-workspace.zip keeps the whole workspace (relative paths, scripts, docs); migrated projects
    replace their originals, the rest stay as they were and the manifest says which is which."""
    from .output import result as res
    from .worker import source as src_mod
    tree = out / '.ws-tree'
    shutil.rmtree(tree, ignore_errors=True)
    src_mod.copy_local(d['dir'], tree)
    status = {}
    for p in d['projects']:
        mp = out / 'projects' / p['name'] / 'migrated-project'
        r = results.get(p['name'], {})
        if mp.exists() and r.get('status') in ('SUCCEEDED', 'PARTIAL_SUCCESS'):
            dest = tree if p['dir'] == '.' else tree / p['dir']
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(mp, dest)
            status[p['name']] = {'status': r['status'], 'content': 'migrated'}
        else:
            status[p['name']] = {'status': r.get('status', 'NOT_RUN'), 'reason': r.get('reason'), 'content': 'original (no migrado)'}
    ok = [n for n, x in status.items() if x['content'] == 'migrated']
    overall = 'SUCCEEDED' if ok and len(ok) == len(status) and all(results[n]['status'] == 'SUCCEEDED' for n in ok) else \
        ('PARTIAL_SUCCESS' if ok else 'FAILED')
    man = res.manifest(status=overall, source='local', target={}, projects=len(d['projects']), build_passed=bool(ok) and len(ok) == len(status),
                       tests_passed=bool(ok) and len(ok) == len(status), manual_actions=0,
                       extra={'workspaceRoot': d['info']['workspaceRoot'], 'projectStatus': status})
    rd = lambda pth: pth.read_text() if pth.exists() else ''
    docs = {'migration-report.md': rd(out / 'workspace' / 'workspace-migration.md') + '\n' + rd(out / 'workspace' / 'workspace-analysis.md'),
            'manual-actions.md': ''.join(f"# {n}\n\n{rd(out / 'projects' / n / 'reports' / 'manual-actions.md')}\n" for n in status)}
    docs |= {f'diff-{n}.patch': rd(out / 'projects' / n / 'reports' / 'candidate.patch') for n in status}
    prefix = '' if d['info']['workspaceRoot'] == '.' else d['info']['workspaceRoot'].rstrip('/') + '/'
    res.build_zip(tree, out / 'migrated-workspace.zip', man, docs, prefix=prefix)
    shutil.rmtree(tree, ignore_errors=True)


def migrate_workspace(source: Path, out: Path, target, java, accepted, *, log, resume=False, retry=(), parallelism=None, **kw) -> int:
    """Doc 20: ANALYSIS WAVES (all projects, context propagated) -> MIGRATION WAVES (RUNNABLE vs BLOCKED_BY_DEPENDENCY,
    parallel inside a wave when configured, a failure never cancels the workspace). Each project has its own
    projects/<name>/{analysis,reports,logs,migrated-project}; legacy and migrated internal artifacts live in isolated
    overlays (per thread). resume/retry reuse workspace/workspace-state.json and never repeat successful projects."""
    from . import workspace
    from .orchestration import migration_wave_orchestrator as mig, multi_project_orchestrator as orch
    from .worker import mavenops, source as src_mod
    state_file = out / 'workspace' / 'workspace-state.json'
    previous = None
    if resume or retry:
        if not state_file.exists():
            raise InvalidInput(f'No hay estado previo para reanudar en {state_file}')
        previous = {n: x['result'] for n, x in json.loads(state_file.read_text())['projects'].items() if x.get('result')}
    elif out.exists() and any(out.iterdir()):
        raise InvalidInput(f'El directorio de salida no está vacío: {out}')
    ws = orch.build_context(source)
    if ws.info['repositoryType'] != workspace.MULTI_PROJECT:
        raise InvalidInput(f"--workspace requiere un {workspace.MULTI_PROJECT}; el origen es {ws.info['repositoryType']}")
    unknown = set(retry) - {p.name for p in ws.projects}
    if unknown:
        raise InvalidInput(f'Proyectos desconocidos en --retry: {sorted(unknown)}')
    out.mkdir(parents=True, exist_ok=True)
    # ANALYSIS WAVES (static: the migration of each project runs its own full analysis with Maven)
    orch.analyze_all(ws, lambda p: analyze(p.root, target, None, False, lambda m: None), log=log)
    legacy, migrated = out / '.m2-legacy', out / '.m2-migrated'
    ctx = engine_context(out / '.ws', 12)

    def install(project_dir: Path, overlay: Path, name: str) -> bool:
        tmp = out / '.ws' / name
        shutil.rmtree(tmp, ignore_errors=True)
        src_mod.copy_local(project_dir, tmp)  # never build inside the source or the deliverable
        mavenops.use_local_overlay(overlay)
        try:
            return mavenops.mvn(ctx, tmp, ['-DskipTests', 'install'], f'install-{name}').exit_code == 0
        finally:
            mavenops.use_local_overlay(None)
            shutil.rmtree(tmp, ignore_errors=True)

    def reused(p):  # a project migrated in a previous attempt: its artifacts must be in the overlays for its dependents
        install(p.root, legacy, f'{p.name}-legacy')
        if (out / 'projects' / p.name / 'migrated-project').exists():
            install(out / 'projects' / p.name / 'migrated-project', migrated, f'{p.name}-migrated')

    def migrate_one(p, label):
        pout = out / 'projects' / p.name
        for x in (pout.iterdir() if pout.exists() else []):  # a retried project starts clean; its analysis is kept
            if x.name != 'analysis':
                shutil.rmtree(x) if x.is_dir() else x.unlink()
        plog = lambda m: log(f'[workspace][migration-{label}][{p.name}][migration] {m}')
        mavenops.use_local_overlay(legacy)  # baseline: legacy internal artifacts (thread-local)
        try:
            code = migrate(p.root, pout, target, java, accepted, log=plog, on_candidate=lambda: mavenops.use_local_overlay(migrated), **kw)
        except InvalidInput as e:  # a structural problem of one project stays in that project
            return {'status': 'FAILED', 'reason': f'PROJECT_BUILD_FAILED: {e}'}
        finally:
            mavenops.use_local_overlay(None)
        rep = pout / 'reports' / 'migration-report.json'
        r = json.loads(rep.read_text()) if rep.exists() else {}
        res = {'status': r.get('status', 'FAILED'), 'reason': r.get('primaryReason') or ('' if r.get('status') in mig.OK else 'PROJECT_BUILD_FAILED'),
               'exitCode': code}
        if r.get('migrationMode') == 'DEGRADED':
            res |= {'migrationMode': 'DEGRADED', 'validationConfidence': (r.get('validationConfidence') or {}).get('level')}
        if r.get('baseline'):
            res['baseline'] = r['baseline']
        if not install(p.root, legacy, f'{p.name}-legacy'):
            res['warning'] = 'no se pudo instalar la versión legacy en el overlay (dependientes: baseline sin ella)'
        if res['status'] in mig.OK and not install(pout / 'migrated-project', migrated, f'{p.name}-migrated'):
            if res.get('migrationMode') == 'DEGRADED':  # doc 32 §21: migrated, but its artifact cannot be built for the dependents
                res['artifactAvailable'] = False
            else:
                res.update(status='FAILED', reason='MIGRATED_ARTIFACT_NOT_INSTALLABLE')
        plog(f"build {'PASS' if res['status'] in mig.OK else 'FAIL'}")
        return res
    from . import workspace as wsmod
    _WORKSPACE['internal'] = frozenset(f'{g}:{a}' for p in ws.projects for g, a in wsmod.coordinates(ws.workspace_root, {
        'dir': str(p.root.relative_to(ws.workspace_root)) or '.'})[0])
    parallelism = parallelism or int(os.environ.get('MF_WORKSPACE_MIGRATION_PARALLELISM', '1'))
    mavenops.set_parallel(parallelism > 1)
    try:
        results, _ = mig.run(ws, migrate_one, parallelism=parallelism, log=log, previous=previous,
                             rerun=set(retry) if retry else None, on_reused=reused if previous else None)
    finally:
        mavenops.set_parallel(False)
        mavenops.use_local_overlay(None)
        _WORKSPACE['internal'] = frozenset()
    status = orch.write_outputs(ws, out, results)
    d = {'dir': ws.workspace_root, 'info': ws.info,
         'projects': [{'name': p.name, 'dir': str(p.root.relative_to(ws.workspace_root)).replace('\\', '/') or '.'} for p in ws.projects]}
    _package_workspace(out, source, d, results)
    for x in (legacy, migrated, out / '.ws'):
        shutil.rmtree(x, ignore_errors=True)
    log(f'[workspace] {ws.analysis_status} · {ws.migration_status} · workspace {status}')
    return EXIT_OK if status in ('SUCCEEDED', 'PARTIAL_SUCCESS') and all(r['status'] in mig.OK for r in results.values()) else EXIT_BUILD


# --- entry point --------------------------------------------------------------------------------
def verify_cmd(args, log) -> int:
    """Equivalence evidence for the tomcat-war profile (docs/19_TOMCAT_WAR.md). Exit: 0 PASS / accepted, 3 differences or
    not comparable (needs review), 4 invalid input."""
    import subprocess
    from .tomcat_war import pipeline as tw, verify
    out = Path(args.output).resolve()
    try:
        if args.check == 'baseline-server':
            cmd, env = verify.baseline_command(Path(args.project), out, [m.strip() for m in args.modules.split(',') if m.strip()], args.servlets,
                                          Path(args.etc), args.port, [c for c in args.classpath.split(os.pathsep) if c], log)
            log(f'línea base en http://localhost:{args.port} (Ctrl+C para detener)')
            return subprocess.run(cmd, env=env).returncode
        if args.check == 'routes':
            res = verify.routes(Path(args.project), Path(args.migrated), out, Path(args.config).resolve() if args.config else None, log)
            print(verify.routes_markdown(res))
        else:
            res = verify.responses(args.old, args.new, Path(args.cases), out, args.timeout, log)
            print(verify.responses_markdown(res).split('## Detalle de las diferencias')[0])
    except tw.InvalidInput as e:
        raise InvalidInput(str(e)) from None
    return EXIT_OK if res['status'] in ('PASS', 'PASS_WITH_ACCEPTED') else EXIT_CRITICAL


def tomcat_war(source: Path, args, log) -> int:
    """Profile tomcat-war (docs/19_TOMCAT_WAR.md): the whole workspace becomes one WAR; `plan` only writes the reports."""
    from .tomcat_war import pipeline as tw
    out = Path(args.output or 'migration-output').resolve()
    try:
        res = tw.migrate(source, out, Path(args.config).resolve() if args.config else None, log=log)
    except tw.InvalidInput as e:
        raise InvalidInput(str(e)) from None
    ws = out / 'migrated-workspace'
    code = EXIT_OK
    if args.cmd == 'plan':
        shutil.rmtree(ws, ignore_errors=True)  # plan: what would change, without leaving a candidate
    else:
        if args.build:
            res['build'] = tw.build(ws, log)
            write_json(out / 'reports' / 'tomcat-war-report.json', res)
            code = EXIT_OK if res['build']['status'] == 'PASS' else EXIT_BUILD
        shutil.make_archive(str(out / 'migrated-workspace'), 'zip', ws)
    print((out / 'reports' / 'tomcat-war-report.md').read_text(encoding='utf-8').split('## Detalle por módulo')[0])
    if 'build' in res:
        print('\n'.join([f"Build: {res['build']['status']}"] + [f'  {e}' for e in res['build'].get('errors', [])]))
    if code == EXIT_OK and args.fail_on_critical and res['status'] != 'SUCCEEDED':
        return EXIT_CRITICAL
    return code


def main(argv=None):
    _env_for_cli()
    ap = argparse.ArgumentParser(prog='migrate.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--verbose', action='store_true')
    common.add_argument('--fail-on-critical', action='store_true', help='exit 3 if critical/blocking manual actions remain')
    a = sub.add_parser('analyze', parents=[common]); a.add_argument('project'); a.add_argument('--output')
    a.add_argument('--workspace', action='store_true', help='analizar cada proyecto de un repositorio multi-proyecto')
    a.add_argument('--discover-projects', action='store_true', help='solo clasificar, grafo interno, orden y oleadas')
    a.add_argument('--project', dest='project_name', help='un proyecto del workspace (nombre o carpeta)')
    a.add_argument('--no-maven', action='store_true', help='static analysis only (no mvn help:effective-pom)')
    pfp = sub.add_parser('preflight', parents=[common]); pfp.add_argument('project'); pfp.add_argument('--output')
    for name in ('plan', 'migrate'):
        p = sub.add_parser(name, parents=[common]); p.add_argument('project')
        p.add_argument('--target', choices=sorted(TARGETS), default='springboot'); p.add_argument('--java', choices=['17', '21'], default='21')
        p.add_argument('--accept', action='append', default=[], metavar='RULE=MOTIVO',
                       help='decisión explícita sobre un hallazgo bloqueante, p. ej. CAMEL2_VERSION="revisado contra guía 2→3"')
        p.add_argument('--output')
        p.add_argument('--project', dest='project_name', help='un proyecto del workspace (nombre o carpeta)')
        p.add_argument('--config', help='--target tomcat-war: JSON con las decisiones del cliente (groupId, contenedores, parches, overlay)')
        p.add_argument('--build', action='store_true', help='--target tomcat-war: compilar el workspace migrado (mvn package)')
        if name == 'migrate':
            p.add_argument('--workspace', action='store_true', help='migrar todos los proyectos del workspace por oleadas')
            p.add_argument('--resume', action='store_true', help='--workspace: reanudar sobre la misma salida (no repite los exitosos)')
            p.add_argument('--retry', action='append', default=[], metavar='PROYECTO', help='--workspace: reintentar solo ese proyecto (y desbloquear sus dependientes)')
            p.add_argument('--parallel', type=int, default=None, help='--workspace: proyectos en paralelo dentro de una oleada')
            p.add_argument('--dry-run', action='store_true'); p.add_argument('--no-autofix', action='store_true')
            p.add_argument('--max-fix-rounds', type=int, default=3); p.add_argument('--skip-baseline', action='store_true')
            p.add_argument('--report-format', default='md,json')
            p.add_argument('--allow-baseline-failure', action='store_true', help='continuar desde BASELINE_FAILED_CODE/TEST (código roto conocido)')
            p.add_argument('--allow-baseline-dependency-failure', action='store_true', help='modo degradado también con dependencias externas de criticidad media')
            p.add_argument('--allow-baseline-network-failure', action='store_true', help='modo degradado ante BASELINE_BLOCKED_NETWORK')
            p.add_argument('--allow-baseline-repository-failure', action='store_true', help='modo degradado ante BASELINE_BLOCKED_REPOSITORY')
            p.add_argument('--auto-apply-up-to', choices=['AUTO', 'AUTO_TEST', 'REVIEW'], default='REVIEW',
                           help='nivel de confianza menos seguro que se aplica sin aprobación (MANUAL nunca se aplica)')
            p.add_argument('--approve', action='append', default=[], metavar='PASO', help='aprobar un paso por debajo del umbral (o un refactor MOD_*)')
            p.add_argument('--modernize', action='store_true', help='tras migrar, modernizar el proyecto migrado (salida en modernization/)')
    v = sub.add_parser('validate', parents=[common]); v.add_argument('project')
    sub.add_parser('plugins', help='contrato de plugins del motor (id, versión, capacidades, orígenes, destinos)')
    mo = sub.add_parser('modernize', parents=[common], help='modernizar código ya migrado (SAFE / REFACTOR / ARCHITECTURE)')
    mo.add_argument('project'); mo.add_argument('--output'); mo.add_argument('--target', choices=sorted(TARGETS))
    mo.add_argument('--approve', action='append', default=[], metavar='REGLA', help='aprobar un refactor que cambia contratos, p. ej. MOD_FIELD_INJECTION')
    mo.add_argument('--auto-apply-up-to', choices=['AUTO', 'AUTO_TEST', 'REVIEW'], default='AUTO_TEST')
    ve = sub.add_parser('verify', help='tomcat-war: evidencia de equivalencia contra el código original (gate G4)')
    vsub = ve.add_subparsers(dest='check', required=True)
    vr = vsub.add_parser('routes', help='estructura de las rutas: original (Camel 2) contra migrado')
    vr.add_argument('project'); vr.add_argument('--migrated', required=True, help='migrated-workspace ya compilado (migrate --build)')
    vr.add_argument('--config'); vr.add_argument('--output', required=True)
    vb = vsub.add_parser('baseline-server', help='levanta el código ORIGINAL sobre Camel 2 + Jetty (primer plano)')
    vb.add_argument('project'); vb.add_argument('--modules', required=True, help='artifactId separados por coma')
    vb.add_argument('--servlets', required=True, help='Nombre=/alias,... (los que registraba OsgiServletRegisterer)')
    vb.add_argument('--etc', required=True, help='carpeta de configuración (file:etc/... del origen)')
    vb.add_argument('--port', type=int, default=18081); vb.add_argument('--classpath', default='', help=f'entradas extra separadas por {os.pathsep!r}')
    vb.add_argument('--output', required=True)
    vp = vsub.add_parser('responses', help='misma petición al original y al migrado; compara código, Content-Type y cuerpo')
    vp.add_argument('--old', required=True); vp.add_argument('--new', required=True); vp.add_argument('--cases', required=True)
    vp.add_argument('--output', required=True); vp.add_argument('--timeout', type=int, default=60)
    args = ap.parse_args(argv)
    log = (lambda m: print(f'[mf] {m}', file=sys.stderr))
    try:
        if args.cmd == 'verify':
            return verify_cmd(args, log)
        if args.cmd == 'plugins':
            from .plugins import PLUGINS
            print(json.dumps(PLUGINS, ensure_ascii=False, indent=2))
            return EXIT_OK
        if args.cmd == 'modernize':
            from .modernization.pipeline import InvalidInput as ModInvalid, run as modernize
            try:
                res = modernize(Path(args.project), Path(args.output or 'modernization-output'), target=TARGETS.get(args.target),
                                approved=set(args.approve), auto_up_to=args.auto_apply_up_to, log=log)
            except ModInvalid as e:
                raise InvalidInput(str(e)) from None
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return EXIT_OK if res['baseline'] == 'PASS' else EXIT_BUILD
        if args.cmd == 'validate':
            from .worker import runner
            proj = Path(args.project).resolve()
            if not (proj / 'pom.xml').exists():
                from . import workspace as wsmod
                info = wsmod.classify_dir(proj) if proj.is_dir() else {}
                if info.get('repositoryType') != wsmod.MULTI_PROJECT:
                    raise InvalidInput(f'No hay pom.xml en {proj}')
                # doc 21: a workspace without aggregator is validated per ProjectContext, never at its root
                print(json.dumps({'workspaceBuild': {'status': 'SKIPPED', 'reason': 'WORKSPACE_BUILD_NOT_APPLICABLE'}}, ensure_ascii=False))
                codes = [main(['validate', str(proj / p['dir'])]) for p in info['projects']]
                return EXIT_OK if all(c == EXIT_OK for c in codes) else EXIT_BUILD
            logs = Path(os.environ.get('TMPDIR', '/tmp')) / f'mf-validate-{uuid.uuid4().hex[:8]}'
            ctx = runner.Context('cli', logs, deadline=time.monotonic() + 3600, emit=None)
            work = logs / 'copy'  # validate on a copy: the given project folder is not modified
            from .worker.source import copy_local
            copy_local(proj, work)
            res, _ = run_validation(ctx, work, 'validate')
            print(json.dumps(res | {'log': str(Path(res['log']))}, ensure_ascii=False, indent=2))
            return EXIT_OK if res['status'] == 'PASS' else EXIT_BUILD
        source = Path(args.project).resolve()
        if not source.is_dir():
            raise InvalidInput(f'No es un directorio: {source}')
        if getattr(args, 'project_name', None):
            source = resolve_project(source, args.project_name)
        elif args.cmd == 'analyze' and (args.workspace or args.discover_projects or discover(source)['info']['repositoryType'] == 'MULTI_PROJECT_REPOSITORY'):
            out = Path(args.output).resolve() if args.output else None
            res = analyze_workspace(source, out, not args.no_maven and not args.discover_projects, log, discover_only=args.discover_projects)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return EXIT_OK
        if args.cmd in ('analyze', 'preflight'):
            from . import preflight
            out = Path(args.output).resolve() if args.output else None
            if out:
                out.mkdir(parents=True, exist_ok=True)
            report = analyze(source, 'spring', out, args.cmd == 'preflight' or not args.no_maven, log, build=args.cmd == 'preflight')
            pf = report['preflight']
            if out:
                write_json(out / 'analysis.json', report)
                write_effort(out, report)
                (out / 'baseline-report.md').write_text(preflight.report_markdown(pf['environment'], pf['model'], pf['repairs'], pf['build'], pf.get('modelCheck')))
            if args.cmd == 'preflight':
                print(preflight.report_markdown(pf['environment'], pf['model'], pf['repairs'], pf['build'], pf.get('modelCheck')))
                st = pf['build'].get('state')
                return {'BASELINE_SUCCESS': EXIT_OK, 'BASELINE_REPAIRED': EXIT_OK, 'BASELINE_FAILED_CODE': EXIT_BUILD,
                        'BASELINE_FAILED_TEST': EXIT_BUILD}.get(st, EXIT_ERROR)
            print(json.dumps({'overview': report['overview'], 'summary': report['summary']}, ensure_ascii=False, indent=2))
            critical = any(f['blocking'] for f in report['findings'])
            return EXIT_CRITICAL if args.fail_on_critical and critical else EXIT_OK
        if args.cmd in ('plan', 'migrate') and args.target == 'tomcat-war':
            return tomcat_war(source, args, log)
        accepted = {}
        for item in args.accept:
            rule, _, why = item.partition('=')
            if not rule or len(why.strip()) < 10:
                raise InvalidInput('--accept requiere RULE="motivo de al menos 10 caracteres"')
            accepted[rule.strip().upper()] = why.strip()
        target = TARGETS[args.target]
        if args.cmd == 'plan':
            report = analyze(source, target, None, True, log)
            plan = build_plan(report, target, args.java, accepted)
            md = plan_markdown(report, plan)
            if args.output:
                o = Path(args.output).resolve(); o.mkdir(parents=True, exist_ok=True)
                (o / 'migration-plan.md').write_text(md)
                write_json(o / 'migration-plan.json', plan)
            print(md)
            blocked = any(s['state'] == 'blocked' for s in plan['steps'])
            return EXIT_CRITICAL if args.fail_on_critical and blocked else EXIT_OK
        out = Path(args.output or 'migration-output').resolve()
        if args.workspace:
            code = migrate_workspace(source, out, target, args.java, accepted, log=log, resume=args.resume, retry=args.retry, parallelism=args.parallel,
                                     dry_run=args.dry_run, autofix=not args.no_autofix,
                                     max_rounds=args.max_fix_rounds, baseline=not args.skip_baseline,
                                     allow_baseline_failure=args.allow_baseline_failure, auto_up_to=args.auto_apply_up_to, approved=set(args.approve),
                       baseline_policy=baseline_policy(args))
            print((out / 'workspace' / 'workspace-migration.md').read_text())
            return code
        code = migrate(source, out, target, args.java, accepted, dry_run=args.dry_run, autofix=not args.no_autofix,
                       max_rounds=args.max_fix_rounds, baseline=not args.skip_baseline, log=log,
                       allow_baseline_failure=args.allow_baseline_failure, auto_up_to=args.auto_apply_up_to, approved=set(args.approve),
                       baseline_policy=baseline_policy(args))
        if args.modernize and code == EXIT_OK and (out / 'migrated-project').exists():
            from .modernization.pipeline import run as modernize
            res = modernize(out / 'migrated-project', out / 'modernization', target=target, approved=set(args.approve), log=log)
            code = EXIT_OK if res['baseline'] == 'PASS' else EXIT_BUILD
        fmts = {f.strip() for f in args.report_format.split(',')}
        if 'json' not in fmts:
            for f in (out / 'reports').glob('*.json'):
                f.unlink()
        if 'md' not in fmts:
            for f in (out / 'reports').glob('*.md'):
                f.unlink()
        if (out / 'reports' / 'checksums.sha256').exists():  # report-format may have removed files
            from .sbom import checksums
            (out / 'reports' / 'checksums.sha256').write_text(checksums(out))
        print((out / 'reports' / 'migration-report.md').read_text() if (out / 'reports' / 'migration-report.md').exists() else '')
        if code == EXIT_OK and args.fail_on_critical:
            plan = json.loads((out / 'reports' / 'migration-plan.json').read_text()) if (out / 'reports' / 'migration-plan.json').exists() else {}
            if any(s['state'] == 'blocked' for s in plan.get('steps', [])):
                return EXIT_CRITICAL
        return code
    except InvalidInput as e:
        log(f'Entrada inválida: {e}')
        return EXIT_INPUT
    except Exception as e:  # noqa: BLE001 — CLI boundary: report, never a raw traceback with possible secrets
        from .security import redact
        log(f'Error: {redact(f"{type(e).__name__}: {e}")}')
        if getattr(args, 'verbose', False):
            raise
        return EXIT_ERROR


if __name__ == '__main__':
    sys.exit(main())

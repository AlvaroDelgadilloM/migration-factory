"""DependencyResolutionClassifier + BaselinePolicyEngine (32-CORRECCION).

"The code is broken" (BASELINE_FAILED_CODE: dependencies resolved, javac ran and failed) is told apart from "the
environment cannot reproduce the baseline" (a Maven dependency could not be resolved: the compiler never ran, the code
is UNKNOWN). The policy decides per category whether analysis and migration continue, degrade or block. Confidence is
reported as a level with its factors, never as a number. Stdlib only."""
from __future__ import annotations

import re

NOT_FOUND = re.compile(r'Could not find artifact ([\w.\-]+:[\w.\-]+:[\w.\-]+(?::[\w.\-]+)?:[\w.\-]+)')
ABSENT = re.compile(r'([\w.\-]+:[\w.\-]+:[\w.\-]+(?::[\w.\-]+)?:[\w.\-]+) \(absent\)')
TRANSFER = re.compile(r'Could not transfer artifact ([\w.\-]+:[\w.\-]+:[\w.\-]+(?::[\w.\-]+)?:[\w.\-]+)')
PARENT = re.compile(r'Non-resolvable parent POM for [^:]+:[^:]+:[^:]+: (?:The following artifacts could not be resolved: )?'
                    r'(?:Could not find artifact )?([\w.\-]+:[\w.\-]+:pom:[\w.\-]+)')
NO_VERSIONS = re.compile(r'No versions available for ([\w.\-]+:[\w.\-]+)')
DEPENDENCY_LINE = re.compile(r'dependency: ([\w.\-]+:[\w.\-]+:[\w.\-]+(?::[\w.\-]+)?:[\w.\-]+) \(\w+\)')       # Maven 3.9 test/runtime phase
WAS_NOT_FOUND = re.compile(r'([\w.\-]+:[\w.\-]+:[\w.\-]+(?::[\w.\-]+)?:[\w.\-]+) was not found in')
CACHED = re.compile(r'was cached in the local repository|resolution is not reattempted until the update interval|during a previous attempt')
AUTH = re.compile(r'status code: 40[13]|\b40[13]\b.*(?:Unauthorized|Forbidden)|Not authorized|authentication failed|authorization failed', re.I)
NETWORK = re.compile(r'Connect(?:ion)? (?:to \S+ )?timed out|UnknownHostException|Unknown host|Connection refused|No route to host|'
                     r'SSLHandshakeException|PKIX path building failed|Network is unreachable', re.I)
POLICY = re.compile(r'Blocked mirror for repositories|maven-default-http-blocker|offline mode', re.I)
DRIVERS = re.compile(r'(ojdbc|db2jcc|jconn|mssql-jdbc|sqljdbc|jtds|mysql-connector|mariadb-java-client|postgresql|ifxjdbc|h2database|hsqldb|derby)', re.I)
FRAMEWORK_GROUPS = ('org.apache.camel', 'org.springframework', 'io.quarkus', 'javax.', 'jakarta.', 'org.jboss.spec', 'org.apache.cxf')
COMMERCIAL_GROUPS = ('com.oracle', 'com.ibm', 'com.sap', 'com.microsoft.sqlserver', 'com.sybase', 'com.informix', 'com.tibco')

STATE = {'ARTIFACT_NOT_FOUND': 'BASELINE_BLOCKED_DEPENDENCY', 'VERSION_NOT_AVAILABLE': 'BASELINE_BLOCKED_DEPENDENCY',
         'CACHED_RESOLUTION_FAILURE': 'BASELINE_BLOCKED_DEPENDENCY', 'PRIVATE_REPOSITORY_REQUIRED': 'BASELINE_BLOCKED_REPOSITORY',
         'REPOSITORY_POLICY_BLOCK': 'BASELINE_BLOCKED_REPOSITORY', 'AUTH_REQUIRED': 'BASELINE_BLOCKED_AUTH',
         'NETWORK_ERROR': 'BASELINE_BLOCKED_NETWORK', 'INTERNAL_ARTIFACT_MISSING': 'BASELINE_BLOCKED_INTERNAL_ARTIFACT'}
STATE_PRIORITY = ('BASELINE_BLOCKED_INTERNAL_ARTIFACT', 'BASELINE_BLOCKED_AUTH', 'BASELINE_BLOCKED_NETWORK', 'BASELINE_BLOCKED_REPOSITORY',
                  'BASELINE_BLOCKED_DEPENDENCY')
BUILD_STATE = {'BASELINE_BLOCKED_DEPENDENCY': 'BUILD_BLOCKED_DEPENDENCY', 'BASELINE_BLOCKED_REPOSITORY': 'BUILD_BLOCKED_REPOSITORY',
               'BASELINE_BLOCKED_AUTH': 'BUILD_BLOCKED_AUTH', 'BASELINE_BLOCKED_NETWORK': 'BUILD_BLOCKED_NETWORK',
               'BASELINE_BLOCKED_INTERNAL_ARTIFACT': 'BUILD_BLOCKED_DEPENDENCY', 'BASELINE_BLOCKED_PLATFORM_BOM': 'BUILD_NOT_EXECUTABLE'}


def _ga(coord: str) -> str:
    return ':'.join(coord.split(':')[:2])


def category(coord: str, internal: set) -> tuple[str, str]:
    """(dependencyCategory, migrationCriticality) of a missing artifact for a Camel migration."""
    g = coord.split(':')[0]
    if _ga(coord) in internal:
        return 'INTERNAL', 'HIGH'
    if g.startswith(FRAMEWORK_GROUPS):
        return 'FRAMEWORK', 'HIGH'      # needed to resolve the types the recipes rewrite
    if DRIVERS.search(coord):
        return 'DATABASE_DRIVER', 'LOW'  # loaded at runtime through JDBC/JNDI, not by Camel recipes
    return 'OTHER', 'MEDIUM'


def classify(log_text: str, internal=frozenset(), org_prefixes=frozenset()) -> list[dict]:
    """Missing artifacts with reason, category and criticality. Empty list: not a dependency-resolution failure."""
    coords = []
    for rx in (PARENT, NOT_FOUND, ABSENT, TRANSFER, DEPENDENCY_LINE, WAS_NOT_FOUND):
        for m in rx.finditer(log_text):
            if m[1] not in coords:
                coords.append(m[1])
    coords += [f'{m[1]}:?' for m in NO_VERSIONS.finditer(log_text) if not any(c.startswith(m[1] + ':') for c in coords)]
    out = []
    for c in coords:
        line = next((l for l in log_text.splitlines() if c.split(':')[1] in l and re.search('Could not|absent|No versions|Non-resolvable|was not found', l)), log_text)
        g = c.split(':')[0]
        if _ga(c) in internal:
            reason = 'INTERNAL_ARTIFACT_MISSING'
        elif AUTH.search(line) or AUTH.search(log_text):
            reason = 'AUTH_REQUIRED'
        elif NETWORK.search(line) or (TRANSFER.search(line) and NETWORK.search(log_text)):
            reason = 'NETWORK_ERROR'
        elif POLICY.search(log_text):
            reason = 'REPOSITORY_POLICY_BLOCK'
        elif CACHED.search(line) or CACHED.search(log_text):
            reason = 'CACHED_RESOLUTION_FAILURE'
        elif c.endswith(':?'):
            reason = 'VERSION_NOT_AVAILABLE'
        elif any(g.startswith(p) for p in org_prefixes):
            reason = 'PRIVATE_REPOSITORY_REQUIRED'  # artifact of the organisation, outside the workspace: lives in a private repository
        else:
            reason = 'ARTIFACT_NOT_FOUND'
        cat, crit = category(c, set(internal))
        out.append({'artifact': c.rstrip(':?'), 'reason': reason, 'baselineStatus': STATE[reason], 'dependencyCategory': cat,
                    'migrationCriticality': crit,
                    'hint': 'mvn -U (solo diagnóstico: el fallo está en caché del repositorio local)' if reason == 'CACHED_RESOLUTION_FAILURE' else
                    ('artefacto comercial: suele estar solo en un repositorio privado; proveer el artefacto o el repositorio aprobado (MF_MAVEN_EXTRA_REPOS)'
                     if g.startswith(COMMERCIAL_GROUPS) else
                     'proveer el artefacto o el repositorio aprobado (MF_MAVEN_EXTRA_REPOS)' if reason in ('PRIVATE_REPOSITORY_REQUIRED', 'ARTIFACT_NOT_FOUND')
                     else None)})
    return out


def baseline_state(build_ok: bool, repairs, errors, issues) -> str:
    if build_ok:
        return 'BASELINE_REPAIRED' if repairs else 'BASELINE_SUCCESS'
    cats = {e['category'] for e in errors}
    if issues:
        states = {i['baselineStatus'] for i in issues}
        return next(s for s in STATE_PRIORITY if s in states)
    if cats & {'ENVIRONMENT', 'BUILD_MODEL', 'ORCHESTRATION_ERROR'}:
        return 'BASELINE_BLOCKED'
    if 'DEPENDENCY' in cats:
        return 'BASELINE_BLOCKED_DEPENDENCY'  # resolution failed but the artifact could not be parsed: still not a code failure
    if 'SOURCE' in cats:
        return 'BASELINE_FAILED_CODE'      # dependencies resolved, javac ran and failed
    if 'TEST' in cats:
        return 'BASELINE_FAILED_TEST'
    return 'BASELINE_UNKNOWN'


DEFAULT_POLICY = {'allowExternalDependencyFailure': True, 'allowMediumCriticality': False, 'allowNetworkFailure': False,
                  'allowAuthFailure': False, 'allowInternalArtifactFailure': False, 'allowRepositoryFailure': False}


def decide(state: str, issues: list[dict], policy: dict | None = None) -> dict:
    """BaselinePolicyEngine: analysis always continues (static); migration CONTINUE / DEGRADED / BLOCKED."""
    p = DEFAULT_POLICY | (policy or {})
    if not state.startswith('BASELINE_BLOCKED_'):
        return {'analysis': 'CONTINUE', 'migration': 'CONTINUE', 'validationConfidence': 'FULL', 'reason': None}
    if state == 'BASELINE_BLOCKED_PLATFORM_BOM':  # doc 42: no valid Maven model -> no stubs, no degraded mode; the plan is still produced
        return {'analysis': 'CONTINUE', 'analysisStatus': 'ANALYSIS_PARTIAL', 'migration': 'BLOCKED', 'validationConfidence': 'NONE',
                'code': 'VENDOR_BOM_UNRESOLVED', 'reason': 'BOM de plataforma del proveedor no resoluble: el modelo Maven no es válido'}
    allowed = {'BASELINE_BLOCKED_DEPENDENCY': p['allowExternalDependencyFailure'], 'BASELINE_BLOCKED_REPOSITORY': p['allowRepositoryFailure'],
               'BASELINE_BLOCKED_NETWORK': p['allowNetworkFailure'], 'BASELINE_BLOCKED_AUTH': p['allowAuthFailure'],
               'BASELINE_BLOCKED_INTERNAL_ARTIFACT': p['allowInternalArtifactFailure']}
    high = [i for i in issues if i['migrationCriticality'] == 'HIGH']
    medium = [i for i in issues if i['migrationCriticality'] == 'MEDIUM']
    blockers = [i['artifact'] for i in issues if not allowed.get(i['baselineStatus'], False)]
    if high or blockers or (medium and not p['allowMediumCriticality']):
        why = (f"dependencia crítica para la migración: {', '.join(i['artifact'] for i in high)}" if high else
               f"política de baseline no permite continuar: {', '.join(blockers)}" if blockers else
               f"dependencia de criticidad media (use --allow-baseline-dependency-failure): {', '.join(i['artifact'] for i in medium)}")
        return {'analysis': 'CONTINUE', 'analysisStatus': 'ANALYSIS_PARTIAL', 'migration': 'BLOCKED', 'validationConfidence': 'NONE',
                'code': 'MIGRATION_BLOCKED_BY_BASELINE_DEPENDENCY', 'reason': why}
    return {'analysis': 'CONTINUE', 'analysisStatus': 'ANALYSIS_PARTIAL', 'migration': 'DEGRADED', 'validationConfidence': 'REDUCED',
            'code': 'MIGRATION_DEGRADED', 'reason': 'dependencias externas ausentes de baja criticidad: se migra sin validar el build',
            'buildValidation': 'BLOCKED'}


def confidence_factors(baseline_reproducible: bool, deps_resolved: bool, compiled: bool | None, tested: bool | None) -> dict:
    """§24 without numbers: which validations actually happened."""
    f = {'baselineReproducible': baseline_reproducible, 'dependenciesResolved': deps_resolved, 'compilePassed': compiled, 'testsPassed': tested}
    level = 'FULL' if all(v is True for v in f.values()) else 'REDUCED'  # NONE is reserved for a blocked migration (no candidate)
    return {'level': level, 'factors': f}


def report_section(project: str, state: str, issues: list[dict], decision: dict) -> str:
    """§17 baseline-report.md block."""
    if not issues:
        return ''
    lines = ['## Resolución de dependencias del baseline', '', '```text', 'Project:', project, '', 'Baseline status:', state]
    for i in issues:
        lines += ['', 'Dependency:', i['artifact'], '', 'Reason:', i['reason'], '', 'Category:', f"{i['dependencyCategory']} (impacto {i['migrationCriticality']})"]
        if i.get('hint'):
            lines += ['', 'Suggested action:', i['hint']]
    lines += ['', 'Compiler executed:', 'NO', '', 'Code compile status:', 'UNKNOWN', '', 'Analysis:', decision['analysis'],
              '', 'Migration:', decision['migration'], '', 'Validation confidence:', decision['validationConfidence'], '```', '',
              decision.get('reason') or '', '',
              'No se reemplazan dependencias automáticamente (p. ej. ojdbc6 → ojdbc11): requiere versión de la BD, compatibilidad JDBC, '
              'restricciones del runtime y licencia del driver (MANUAL_REVIEW).']
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':  # self-check: acceptance cases 1-4
    ora = ('[ERROR] Failed to execute goal on project common-datasource: Could not resolve dependencies for project '
           'com.posadas.common:common-datasource:jar:1.0: The following artifacts could not be resolved: com.oracle:ojdbc6:jar:11.2.0.4 (absent): '
           'Could not find artifact com.oracle:ojdbc6:jar:11.2.0.4 in mf-approved (https://repo1.maven.org/maven2)')
    i = classify(ora)
    assert {k: i[0][k] for k in ('artifact', 'reason', 'baselineStatus', 'dependencyCategory', 'migrationCriticality')} == {   # doc 32 §15/§17
        'artifact': 'com.oracle:ojdbc6:jar:11.2.0.4', 'reason': 'ARTIFACT_NOT_FOUND', 'baselineStatus': 'BASELINE_BLOCKED_DEPENDENCY',
        'dependencyCategory': 'DATABASE_DRIVER', 'migrationCriticality': 'LOW'} and 'comercial' in i[0]['hint']
    assert decide('BASELINE_BLOCKED_DEPENDENCY', i)['migration'] == 'DEGRADED'
    org = classify(ora.replace('com.oracle:ojdbc6:jar:11.2.0.4', 'com.posadas.legacy:legacy-client:jar:3'), org_prefixes={'com.posadas'})
    assert org[0]['reason'] == 'PRIVATE_REPOSITORY_REQUIRED' and org[0]['baselineStatus'] == 'BASELINE_BLOCKED_REPOSITORY'
    gen = ora.replace('com.oracle:ojdbc6:jar:11.2.0.4', 'org.acme:legacy-util:jar:1.2')
    assert classify(gen)[0]['reason'] == 'ARTIFACT_NOT_FOUND' and baseline_state(False, [], [{'category': 'DEPENDENCY'}], classify(gen)) == 'BASELINE_BLOCKED_DEPENDENCY'  # 1
    assert baseline_state(False, [], [{'category': 'SOURCE'}], []) == 'BASELINE_FAILED_CODE'                                                                       # 2
    d = decide('BASELINE_BLOCKED_DEPENDENCY', [dict(classify(gen)[0], migrationCriticality='LOW')])
    assert d['migration'] == 'DEGRADED' and d['validationConfidence'] == 'REDUCED'                                                                                   # 3
    assert decide('BASELINE_BLOCKED_REPOSITORY', org)['migration'] == 'BLOCKED'
    assert decide('BASELINE_BLOCKED_REPOSITORY', org, {'allowRepositoryFailure': True, 'allowMediumCriticality': True})['migration'] == 'DEGRADED'
    internal = classify(ora.replace('com.oracle:ojdbc6:jar:11.2.0.4', 'com.posadas.common:common-catalogue:jar:1.0'), internal={'com.posadas.common:common-catalogue'})
    assert internal[0]['reason'] == 'INTERNAL_ARTIFACT_MISSING' and baseline_state(False, [], [], internal) == 'BASELINE_BLOCKED_INTERNAL_ARTIFACT'                 # 4
    assert classify('Could not find artifact a:b:jar:1 in central was cached in the local repository, resolution is not reattempted until the update interval')[0]['reason'] == 'CACHED_RESOLUTION_FAILURE'
    assert classify('Could not transfer artifact a:b:pom:1 from/to central: status code: 401, reason phrase: Unauthorized')[0]['reason'] == 'AUTH_REQUIRED'
    assert classify('Could not transfer artifact a:b:pom:1 from/to central: Connect to repo:443 timed out')[0]['reason'] == 'NETWORK_ERROR'
    assert decide('BASELINE_BLOCKED_DEPENDENCY', [{'artifact': 'org.apache.camel:camel-x:jar:2', 'baselineStatus': 'BASELINE_BLOCKED_DEPENDENCY',
                                                    'migrationCriticality': 'HIGH'}])['code'] == 'MIGRATION_BLOCKED_BY_BASELINE_DEPENDENCY'
    assert confidence_factors(False, False, None, None)['level'] == 'REDUCED' and confidence_factors(True, True, True, True)['level'] == 'FULL'
    real = ('[ERROR] Failed to execute goal on project orders-routes: Could not resolve dependencies for project demo.pilot:orders-routes:war:1.0.0\n'
            '[ERROR] dependency: com.oracle:ojdbc6:jar:11.2.0.4 (runtime)\n[ERROR] \tcom.oracle:ojdbc6:jar:11.2.0.4 was not found in '
            'https://repo1.maven.org/maven2 during a previous attempt. This failure was cached in the local repository and resolution is not '
            'reattempted until the update interval of mf-approved has elapsed or updates are forced\n')
    r = classify(real)
    assert [(x['artifact'], x['reason']) for x in r] == [('com.oracle:ojdbc6:jar:11.2.0.4', 'CACHED_RESOLUTION_FAILURE')] and 'mvn -U' in r[0]['hint']
    print('ok')

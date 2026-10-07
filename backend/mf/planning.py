"""Plan generation and evaluation.

A plan is derived from a scan, its findings and a versioned target profile. Steps are stored once;
their blocked/ready state is evaluated at read time from current finding decisions, so a reviewed
finding unblocks a step without rewriting the plan. Recipes are composed only from recipe names
verified in the pinned artifacts (see profiles.py).
"""
import hashlib
import json
import re
from pathlib import PurePosixPath

from sqlalchemy import select

from .models import Finding, Module, Plan, PlanStep, Route, Scan, TargetProfile

CLOSED = {'resolved', 'discarded'}
MANUAL_GROUPS = {
    'CAMEL_VM': 'Comunicación vm:/direct-vm: entre CamelContext',
    'EJB': 'Componentes EJB',
    'JTA': 'Transacciones JTA/XA',
    'JBOSS_SECURITY': 'Seguridad JBoss (realms/roles)',
    'PACKAGING_EAR': 'Separación de EAR',
    'UNMAPPED_COMPONENT': 'Dependencias Camel sin equivalente verificado',
    'TARGET_RUNTIME_MIGRATION_REQUIRED': 'Migración de runtime OSGi/Blueprint → runtime destino',
}
RUNTIME_MIGRATION_PLAN = [  # doc 27: what moving off OSGi/Karaf means, besides Camel 2/3 -> 4
    'packaging bundle → jar; quitar maven-bundle-plugin y las instrucciones Import-Package/Export-Package/Bundle-SymbolicName',
    'beans de Blueprint (OSGI-INF/blueprint/*.xml) → @Configuration/@Bean (Spring Boot) o CDI/@ApplicationScoped (Quarkus)',
    'CamelContext de Blueprint → CamelContext del runtime (RouteBuilder como beans; camel-blueprint se elimina, nunca se sube de versión)',
    'referencias a servicios OSGi (reference/service) → beans o clientes inyectados; Config Admin (cm:property-placeholder) → application.yml',
    'metadatos MANIFEST.MF y features de Karaf → configuración del artefacto ejecutable del runtime',
]
BEHAVIOR_REVIEW = {'CAMEL_SERVLET', 'HARDCODED_URL', 'DYNAMIC_HTTP', 'CAMEL_HTTP_HEADERS', 'MONEY_DOUBLE', 'TEST_COVERAGE', 'JAVA_LEGACY', 'CXF', 'CDI_SCOPE', 'HARDCODED_SECRET', 'QUARTZ2', 'JMS', 'JNDI', 'JBOSS', 'JBOSS_DEPLOYMENT', 'HTTP4', 'CAMEL_CDI', 'SPRING_XML', 'CAMEL_MOVED_API', 'PACKAGING_WAR'}
ROLLBACK_RECIPE = ('El candidato vive en una copia aislada restaurada desde el snapshot, con un commit interno por paso: si la receta falla se hace '
                   'git reset al commit anterior y los pasos dependientes se omiten. Descartar la ejecución elimina '
                   'el candidato; el origen (Git, carpeta o ZIP) nunca se modifica.')


def _yaml(v):
    return json.dumps(v, ensure_ascii=False)  # JSON scalars are valid YAML flow scalars


def recipe_yaml(name, display, items):
    lines = ['---', 'type: specs.openrewrite.org/v1beta/recipe', f'name: {name}', f'displayName: {_yaml(display)}', 'recipeList:']
    for recipe, opts in items:
        if not opts:
            lines.append(f'  - {recipe}')
            continue
        lines.append(f'  - {recipe}:')
        lines += [f'      {k}: {_yaml(v)}' for k, v in opts.items()]
    return '\n'.join(lines) + '\n'


def _package_of(java_file: str) -> tuple[str, str] | None:
    p = PurePosixPath(java_file)
    parts = p.parts
    for i in range(len(parts) - 3):
        if parts[i:i + 3] == ('src', 'main', 'java'):
            return '/'.join(parts[:i]) or '.', '.'.join(parts[i + 3:-1])
    return None


def _spring_app(pkg):
    return (f'package {pkg};\n\nimport org.springframework.boot.SpringApplication;\n'
            'import org.springframework.boot.autoconfigure.SpringBootApplication;\n\n'
            '/** Generado por Migration Factory: punto de entrada Spring Boot. Revisar. */\n'
            '@SpringBootApplication\npublic class Application {\n'
            '    public static void main(String[] args) {\n        SpringApplication.run(Application.class, args);\n    }\n}\n')


def _yaml_from_props(props: list[str]) -> str:
    """'a.b.c=v' lines -> nested YAML (only keys we generate; values are simple scalars)."""
    tree = {}
    for p in props:
        if p.startswith('#') or '=' not in p:
            continue
        k, v = p.split('=', 1)
        node = tree
        parts = k.split('.')
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = v
    out = []

    def walk(node, depth):
        for k, v in node.items():
            if isinstance(v, dict):
                out.append('  ' * depth + f'{k}:')
                walk(v, depth + 1)
            else:
                out.append('  ' * depth + f'{k}: {_yaml(v)}')
    walk(tree, 0)
    return '\n'.join(out) + '\n'


def _test_framework(mod) -> str | None:
    deps = {(d['groupId'], d['artifactId']) for d in mod.dependencies}
    if any(g == 'org.junit.jupiter' for g, _ in deps) or ('org.springframework.boot', 'spring-boot-starter-test') in deps:
        return 'junit5'
    if ('junit', 'junit') in deps:
        return 'junit4'
    return None


def _smoke_test(pkg, builders, route_ids, framework):
    j5 = framework == 'junit5'
    adds = '\n'.join(f'        context.addRoutes(new {b["package"] + "." if b["package"] else ""}{b["class"]}());' for b in builders)
    ids = ', '.join(_yaml(i) for i in route_ids)
    return (f'package {pkg};\n\n'
            + ('import static org.junit.jupiter.api.Assertions.assertTrue;\n' if j5 else 'import static org.junit.Assert.assertTrue;\n')
            + '\nimport java.util.Set;\nimport java.util.stream.Collectors;\nimport org.apache.camel.impl.DefaultCamelContext;\n'
            + 'import org.apache.camel.model.RouteDefinition;\n'
            + ('import org.junit.jupiter.api.Test;\n' if j5 else 'import org.junit.Test;\n')
            + '\n/** Generado por Migration Factory: presencia de rutas Java. No arranca el contexto ni contacta sistemas externos;\n'
            + ' *  no sustituye las pruebas de equivalencia. */\npublic class MigratedRoutesSmokeTest {\n    @Test\n'
            + '    public void routesArePresent() throws Exception {\n        DefaultCamelContext context = new DefaultCamelContext();\n'
            + adds + '\n        Set<String> ids = context.getRouteDefinitions().stream().map(RouteDefinition::getRouteId).collect(Collectors.toSet());\n'
            + f'        assertTrue({_yaml("se esperaban rutas: ")} + ids, ids.size() >= {len(builders)});\n'
            + f'        for (String id : new String[] {{{ids}}}) {{\n'
            + f'            assertTrue({_yaml("ruta ausente: ")} + id, ids.contains(id));\n        }}\n    }}\n}}\n')


def _routes_config(pkg, builders):
    beans = '\n\n'.join(f'    @Bean\n    public {b["package"] + "." if b["package"] else ""}{b["class"]} {b["class"][0].lower() + b["class"][1:]}() {{\n'
                         f'        return new {b["package"] + "." if b["package"] else ""}{b["class"]}();\n    }}' for b in builders)
    return (f'package {pkg};\n\nimport org.springframework.context.annotation.Bean;\nimport org.springframework.context.annotation.Configuration;\n\n'
            '/** Generado por Migration Factory: registra los RouteBuilder legacy como beans para que Camel Spring Boot los cargue.\n'
            ' *  Sustituye al descubrimiento de JBoss/CDI; revisar antes de aprobar. */\n@Configuration\npublic class MigratedRoutesConfig {\n'
            + beans + '\n}\n')


def _verified_target(profile, group, artifact):
    """Starter/extension for a Camel component only if it is managed by the profile's verified BOM snapshot (doc 19)."""
    from .camel_compat import bom
    m = profile.dependency_mapping
    if group != 'org.apache.camel' or not artifact.startswith('camel-') or not m.get('coreComponent'):
        return None
    candidate = m['coreComponent'].format(artifact[len('camel-'):])
    for coord in profile.bom_coordinates:
        snap = bom(coord)
        if snap and f"{m['groupId']}:{candidate}" in snap:
            return candidate
    return None


def runtime_recipe(profile: TargetProfile, modules, routes, findings_by_rule, components=(), route_builders=(), renames=None, removed=None):
    """Return (recipe dict, preconditions, notes, unmapped deps)."""
    m = profile.dependency_mapping
    pre, notes, unmapped, items = [], [], [], []
    for coord in profile.bom_coordinates:
        g, a, v = coord.split(':')
        items.append(('org.openrewrite.maven.AddManagedDependency',
                      {'groupId': g, 'artifactId': a, 'version': v, 'type': 'pom', 'scope': 'import', 'addToRootPom': True}))
    from .profiles import REMOVED_IN_CAMEL4, RENAMED_IN_CAMEL4, SERVER_APIS
    items.append(('org.openrewrite.maven.UpgradeDependencyVersion',  # align an imported camel-bom with the profile
                  {'groupId': 'org.apache.camel', 'artifactId': 'camel-bom', 'newVersion': profile.camel_version}))
    seen = set()
    for mod in modules:
        for d in mod.dependencies:
            api = SERVER_APIS.get((d['groupId'], d['artifactId']))
            if api and (d['groupId'], d['artifactId']) not in seen:
                seen.add((d['groupId'], d['artifactId']))
                items.append(('org.openrewrite.maven.ChangeDependencyGroupIdAndArtifactId',
                              {'oldGroupId': d['groupId'], 'oldArtifactId': d['artifactId'], 'newGroupId': api[0],
                               'newArtifactId': api[1], 'newVersion': api[2]}))
                notes.append(f"API del servidor {d['groupId']}:{d['artifactId']} → {api[0]}:{api[1]}:{api[2]} (scope conservado).")
            if not d['groupId'].startswith('org.apache.camel') or d['artifactId'] in (REMOVED_IN_CAMEL4 if removed is None else removed):
                continue
            if d['groupId'] == m['groupId'] and d['artifactId'] in set(m['artifacts'].values()):
                if ('target', d['artifactId']) not in seen:  # already a target-runtime artifact (e.g. Camel 3 starter): align version
                    seen.add(('target', d['artifactId']))
                    items.append(('org.openrewrite.maven.UpgradeDependencyVersion',
                                  {'groupId': d['groupId'], 'artifactId': d['artifactId'], 'newVersion': m['version']}))
                continue
            name = (RENAMED_IN_CAMEL4 if renames is None else renames).get(d['artifactId'], d['artifactId'])  # renamed by camel-3-to-4
            key = f"{d['groupId']}:{name}"
            d = dict(d, artifactId=name)
            target = m['artifacts'].get(key) or _verified_target(profile, d['groupId'], name)
            if target is None:
                unmapped.append((mod, d))
            elif key not in seen:
                seen.add(key)
                items.append(('org.openrewrite.maven.ChangeDependencyGroupIdAndArtifactId',
                              {'oldGroupId': d['groupId'], 'oldArtifactId': d['artifactId'], 'newGroupId': m['groupId'],
                               'newArtifactId': target, 'newVersion': m['version'], 'changeManagedDependency': True}))
    uses_core = any(d['groupId'] == 'org.apache.camel' and d['artifactId'] == 'camel-core' for mod in modules for d in mod.dependencies)
    if m.get('coreComponent'):
        from .profiles import CORE_COMPONENTS, USED_COMPONENTS
        used = sorted((set(components) & set(CORE_COMPONENTS) if uses_core else set())
                      | {USED_COMPONENTS[c] for c in components if c in USED_COMPONENTS})
        for c in used:  # Camel 2 camel-core/JBoss modules bundled these; Camel 3+ ships them separately (AddDependency is idempotent)
            items.append(('org.openrewrite.maven.AddDependency',
                          {'groupId': m['groupId'], 'artifactId': m['coreComponent'].format(c), 'version': m['version'],
                           'onlyIfUsing': 'org.apache.camel..*'}))
        if used:
            notes.append(f"Componentes core usados añadidos explícitamente: {', '.join(used)}.")
    if 'cxfrs' in components:  # Camel 4 split camel-cxf into cxf-soap and cxf-rest
        if profile.runtime == 'spring':
            items.append(('org.openrewrite.maven.AddDependency', {'groupId': m['groupId'], 'artifactId': 'camel-cxf-rest-starter',
                          'version': m['version'], 'onlyIfUsing': 'org.apache.camel..*'}))
            notes.append('cxfrs: añadido camel-cxf-rest-starter (Camel 4 separa SOAP y REST).')
        else:
            pre.append({'code': 'CXF_REST_SUPPORTED', 'description': 'CXF REST (cxfrs) soportado por el runtime', 'ok': False,
                        'detail': 'Camel Quarkus no ofrece cxfrs: rediseñar con platform-http/rest DSL'})
    if profile.runtime == 'spring':
        if findings_by_rule.get('CDI_SCOPE'):
            for old in ('jakarta.enterprise.context.ApplicationScoped', 'javax.enterprise.context.ApplicationScoped'):
                items.append(('org.openrewrite.java.ChangeType', {'oldFullyQualifiedTypeName': old,
                              'newFullyQualifiedTypeName': 'org.springframework.stereotype.Component', 'ignoreDefinition': True}))
            notes.append('@ApplicationScoped → @Component (singleton de Spring): revisar proxies/lazy y otros ámbitos CDI (@RequestScoped, @Dependent, @Produces) a mano.')
        if findings_by_rule.get('JAKARTA_INJECT'):  # without the API at runtime Spring silently ignores @Inject
            items.append(('org.openrewrite.maven.AddDependency', {'groupId': 'jakarta.inject', 'artifactId': 'jakarta.inject-api',
                          'version': '2.0.1', 'onlyIfUsing': 'jakarta.inject.*'}))
            notes.append('jakarta.inject-api 2.0.1 en runtime para que Spring procese @Inject.')
    deployables = [mod for mod in modules if mod.packaging in ('war', 'jar') and any(
        d['groupId'].startswith('org.apache.camel') for d in mod.dependencies)]
    pre.append({'code': 'SINGLE_DEPLOYABLE', 'description': 'Un único módulo desplegable con Camel',
                'ok': len(deployables) == 1, 'detail': ', '.join(x.path for x in deployables) or 'ninguno'})
    pre.append({'code': 'COMPONENTS_MAPPED', 'description': 'Toda dependencia Camel tiene equivalente verificado en el perfil',
                'ok': not unmapped, 'detail': ', '.join(sorted({d['artifactId'] for _, d in unmapped})) or 'ok'})
    if len(deployables) == 1:
        mod = deployables[0]
        mod_dir = str(PurePosixPath(mod.path).parent)
        prefix = '' if mod_dir == '.' else mod_dir + '/'
        if mod.packaging == 'war':
            items.append(('org.openrewrite.maven.ChangePackaging',
                          {'groupId': mod.group_id, 'artifactId': mod.artifact_id, 'packaging': 'jar'}))
            for desc in ('jboss-deployment-structure.xml', 'jboss-web.xml'):  # server-only descriptors, useless in a JAR
                items.append(('org.openrewrite.DeleteSourceFiles', {'filePattern': f'{prefix}**/WEB-INF/{desc}'}))
            notes.append('Empaquetado war→jar; se eliminan jboss-deployment-structure.xml y jboss-web.xml (revisar sus módulos/dependencias antes de aprobar el diff).')
        spring_xml = {f.file for f in findings_by_rule.get('SPRING_XML', [])}
        xml_routes = sorted({r.file for r in routes if r.dsl == 'xml' and r.file.startswith(prefix) and r.file not in spring_xml})
        for f in sorted({r.file for r in routes if r.dsl == 'xml' and r.file in spring_xml}):
            notes.append(f'{f} es Spring XML (<beans><camelContext>): no se carga como rutas; requiere @ImportResource + camel-spring-xml o conversión revisada.')
        servlet_used = 'servlet' in components or any(d['artifactId'] == 'camel-servlet' for d in mod.dependencies)
        if servlet_used:
            ctx_paths = sorted({b.get('restContextPath') for b in route_builders if b.get('restContextPath')})
            pattern = (ctx_paths[0].rstrip('/') + '/*') if ctx_paths else '/camel/*'
            if profile.runtime == 'spring':
                items.append(('org.openrewrite.maven.AddDependency', {'groupId': 'org.springframework.boot', 'artifactId': 'spring-boot-starter-web',
                              'version': profile.runtime_version, 'onlyIfUsing': 'org.apache.camel..*'}))
                props_extra = [f'camel.servlet.mapping.context-path={pattern}']
            else:
                props_extra = [f'quarkus.camel.servlet.url-patterns={pattern}']
            notes.append(f'REST sobre servlet: mapeo {pattern} (de restConfiguration().contextPath); web.xml de JBoss queda sin uso: revisar.')
        else:
            props_extra = []
        props = list(props_extra)
        if xml_routes:
            items.append(('org.openrewrite.maven.AddDependency',
                          {'groupId': m['groupId'], 'artifactId': m['xmlDsl'], 'version': m['version'], 'onlyIfUsing': 'org.apache.camel..*'}))
            classpath = [f"classpath:{f.split('src/main/resources/', 1)[1]}" for f in xml_routes if 'src/main/resources/' in f]
            if len(classpath) != len(xml_routes):
                notes.append('Hay rutas XML fuera de src/main/resources: no se cargarán automáticamente.')
            if classpath:
                props.append(f"{m['routesProperty']}={','.join(classpath)}")
            notes.append('XML DSL se conserva; no se convierte a Java DSL.')
        java_routes = [_package_of(r.file) for r in routes if r.dsl == 'java' and r.file.startswith(prefix)]
        pkgs = sorted({p[1] for p in java_routes if p and p[1]})
        builders = [b for b in route_builders if b['file'].startswith(prefix)]
        usable = [b for b in builders if b['noArgConstructor']]
        for b in builders:
            if not b['noArgConstructor']:
                notes.append(f"{b['class']} no tiene constructor sin argumentos: registrarlo como bean manualmente.")
        pkg = min(pkgs, key=len) if pkgs else (min((b['package'] for b in usable), key=len) if usable else '')
        if profile.runtime == 'spring':
            if pkg:
                items.append(('org.openrewrite.text.CreateTextFile',
                              {'relativeFileName': f"{prefix}src/main/java/{pkg.replace('.', '/')}/Application.java",
                               'fileContents': _spring_app(pkg), 'overwriteExisting': False}))
            if pkg and usable:
                items.append(('org.openrewrite.text.CreateTextFile',
                              {'relativeFileName': f"{prefix}src/main/java/{pkg.replace('.', '/')}/MigratedRoutesConfig.java",
                               'fileContents': _routes_config(pkg, usable), 'overwriteExisting': False}))
                notes.append(f"RouteBuilder registrados como beans en MigratedRoutesConfig: {', '.join(b['class'] for b in usable)}.")
            items.append(('org.openrewrite.text.CreateTextFile',
                          {'relativeFileName': f'{prefix}src/main/resources/application.yml',
                           'fileContents': '# Generado por Migration Factory. Externalizar datasource/broker con referencias, sin secretos.\n'
                                           + _yaml_from_props(props), 'overwriteExisting': False}))
            notes.append('Configuración en application.yml (si ya existía, no se sobrescribe: revisar).')
        else:
            notes.append('Camel Quarkus descubre RouteBuilder automáticamente; la validación nativa no es parte de este perfil.')
            props.insert(0, '# Generado por Migration Factory. Externalizar datasource/broker con referencias, sin secretos.')
            items.append(('org.openrewrite.text.CreateTextFile',
                          {'relativeFileName': f'{prefix}src/main/resources/application.properties',
                           'fileContents': '\n'.join(props) + '\n', 'overwriteExisting': False}))
        if pkg and usable:
            ids = sorted({r.route_id for r in routes if r.dsl == 'java' and r.route_id and any(r.file == b['file'] for b in usable)})
            framework = _test_framework(mod)
            if framework is None:
                test_dep = ('org.springframework.boot', 'spring-boot-starter-test') if profile.runtime == 'spring' else ('io.quarkus', 'quarkus-junit5')
                items.append(('org.openrewrite.maven.AddDependency', {'groupId': test_dep[0], 'artifactId': test_dep[1],
                              'version': profile.runtime_version, 'scope': 'test', 'onlyIfUsing': 'org.apache.camel..*'}))
                framework = 'junit5'
            items.append(('org.openrewrite.text.CreateTextFile',
                          {'relativeFileName': f"{prefix}src/test/java/{pkg.replace('.', '/')}/MigratedRoutesSmokeTest.java",
                           'fileContents': _smoke_test(pkg, usable, ids, framework), 'overwriteExisting': False}))
            notes.append(f'Prueba de humo generada (MigratedRoutesSmokeTest, {framework}): presencia de rutas {ids or "(sin routeId literal)"}.')
    if findings_by_rule.get('JNDI'):
        notes.append('Lookups JNDI (java:jboss/...) no se reemplazan automáticamente: configurar el DataSource del runtime y conservar el nombre del bean.')
    name = f'mf.runtime.{profile.runtime.capitalize()}'
    return ({'activeRecipes': [name], 'yaml': recipe_yaml(name, f'Runtime {profile.name}', items)}, pre, notes, unmapped)


def build_steps(scan: Scan, profile: TargetProfile, modules, routes, findings, java_version: str | None = None, endpoints=()):
    by_rule = {}
    for f in findings:
        by_rule.setdefault(f.rule_id, []).append(f)
    ids = lambda rules: [str(f.id) for r in rules for f in by_rule.get(r, [])]
    tooling = profile.tooling
    steps = []

    def add(key, title, kind, classification, **kw):
        steps.append({'key': key, 'title': title, 'kind': kind, 'classification': classification, 'gate': kw.get('gate'),
                      'recipe': kw.get('recipe'), 'preconditions': kw.get('preconditions', []), 'finding_ids': kw.get('finding_ids', []),
                      'depends_on': kw.get('depends_on', []), 'rollback': kw.get('rollback', ''), 'notes': kw.get('notes', [])})

    base_pre = [{'code': 'PROFILE_VALIDATED', 'description': 'Perfil objetivo validado', 'ok': profile.status == 'validated', 'detail': profile.status},
                {'code': 'SNAPSHOT_PINNED', 'description': 'Scan asociado a un snapshot inmutable (hash de contenido)', 'ok': bool(scan.snapshot_hash),
                 'detail': (scan.snapshot_hash or '')[:16] + (f' · commit {scan.commit_sha}' if scan.commit_sha else '')},
                {'code': 'POM_PARSED', 'description': 'POMs parseados sin errores', 'ok': not any(e['code'] in ('INVALID_POM', 'INVALID_XML') and e['file'].endswith('pom.xml') for e in scan.errors), 'detail': ''}]
    deps_resolved = (scan.maven_resolution or '').startswith('maven-effective') or all(m.resolution == 'static-local' for m in modules)
    g1 = {'code': 'DEPENDENCIES_RESOLVED', 'description': 'Versiones de dependencias resueltas (Maven efectivo o resolución local completa)',
          'ok': deps_resolved, 'detail': scan.maven_resolution}

    add('baseline', 'Línea base: compilar y probar el commit original', 'validation', 'AUTO', gate='G0',
        preconditions=base_pre[1:2], notes=['Las fallas de línea base se registran y se distinguen de regresiones; no se aceptan automáticamente.'])
    camel_dep = None
    if by_rule.get('CAMEL2_VERSION'):
        add('camel-2-to-3', 'Revisión manual Camel 2→3 (sin receta verificada)', 'manual', 'REVIEW',
            finding_ids=ids(['CAMEL2_VERSION']), notes=['Revisar https://camel.apache.org/manual/camel-3-migration-guide.html. '
                                                        'El paso se libera cuando un arquitecto resuelve el hallazgo con motivo.'])
        camel_dep = 'camel-2-to-3'
    camel_target = tooling['camelRecipeTarget']
    from . import camel_compat
    present = {d['artifactId'] for mod in modules for d in mod.dependencies if d['groupId'] == 'org.apache.camel'}
    usage = dict((scan.summary or {}).get('usage') or {})
    if not usage:  # scans made before usage signals existed: derive what is known, never assume "unused"
        usage['org.apache.camel.cdi'] = [f.file for f in by_rule.get('CAMEL_CDI', []) if f.file.endswith('.java')]
        usage.update({f'{c}:': ['(componentes del análisis)'] for c in (scan.components or []) if c in ('cxf', 'cxfrs')})
    # doc 19: every Camel artifact is classified and its target verified against the target camel-bom before any POM write
    summary = scan.summary or {}
    packagings = sorted({m.packaging for m in modules if m.packaging})
    decisions = [camel_compat.decide(a, usage, camel_target, evidence=summary.get('usageEvidence'), endpoints=endpoints, packagings=packagings,
                                     platform=(summary.get('platform') or {}).get('platform'), tests=summary.get('testClasses'))
                 for a in sorted(present)]
    art_notes, art_rules = [], []
    camel_pre = list(base_pre)
    for d in decisions:
        if d['action'] == 'RENAME':
            art_rules.append(d.get('rule') or f"CAMEL_COMPAT:{d['artifact']}")
            art_notes.append(f"{d['artifact']} → {d['target']} ({d['status']}, confianza {d['confidence']}).")
        elif d['action'] == 'REMOVE':
            art_rules.append(f"CAMEL_COMPAT:{d['artifact']}")
            art_notes.append(f"{d['artifact']} eliminado ({d['status']}): {d['reason']}")
        elif d['action'] == 'BLOCK':  # blocking finding: resolvable only with a recorded reason; the dependency gate still applies
            decided = [f for f in by_rule.get('CAMEL_COMPONENT_COMPAT', []) if (f.evidence or '').startswith(f"org.apache.camel:{d['artifact']} ")]
            camel_pre.append({'code': d['code'], 'description': f"Componente {d['artifact']} con destino válido en Camel {camel_target}",
                              'ok': bool(decided) and all(f.status in CLOSED for f in decided), 'detail': d['reason']})
    # doc 24: PHASE 1 official Camel recipe alone; PHASE 2 compatibility mapping as a separate step (own snapshot, diff
    # and rollback). Phase 2 is a located POM edit, so it never needs OpenRewrite to resolve artifacts that do not exist.
    camel_recipe = {'activeRecipes': [tooling['camelRecipe']], 'compat': decisions,
                    'contract': {'writes': ['pom.properties', 'pom.dependencies', 'java.sources'], 'reads': ['camel.version']}}
    add('camel-3-to-4', f'Recetas Camel Upgrade hasta {camel_target} (receta oficial)', 'recipe', 'AUTO_TEST',
        recipe=camel_recipe, preconditions=camel_pre, depends_on=[camel_dep] if camel_dep else [], finding_ids=ids(['CAMEL_COMPONENT_COMPAT']),
        rollback=ROLLBACK_RECIPE, notes=['La receta cubre Camel 3.x→4.x; no cubre JBoss ni la migración 2→3.',
                                         'Fase 1 de 2: después se normaliza el POM y se aplica el mapeo de compatibilidad.'])
    mapping_dep = 'camel-3-to-4'
    if present:
        add('camel4-compat-mapping', 'Mapeo de compatibilidad Camel 4 (renombres, retiros y alineación de versiones)', 'transform', 'AUTO_TEST',
            recipe={'pomMapping': {'decisions': [d for d in decisions if d['action'] in ('RENAME', 'REMOVE')], 'targetVersion': camel_target},
                    'rules': art_rules, 'contract': {'writes': ['pom.dependencies', 'pom.dependencyManagement'], 'reads': ['camel.version']}},
            preconditions=camel_pre, depends_on=['camel-3-to-4'], rollback=ROLLBACK_RECIPE,
            notes=['Fase 2 de 2: edición localizada del POM según el registro de compatibilidad; versiones literales Camel 2/3 de artefactos '
                   'soportados se alinean a la versión destino; después normalización y deduplicación del POM.'] + art_notes)
        mapping_dep = 'camel4-compat-mapping'
    if any('javax.enterprise' in (f.evidence or '') for f in by_rule.get('CDI_SCOPE', [])):
        add('jakarta-cdi', 'Jakarta CDI (javax.enterprise → jakarta.enterprise)', 'recipe', 'AUTO_TEST',
            recipe={'activeRecipes': ['org.openrewrite.java.migrate.jakarta.JavaxEnterpriseToJakartaEnterprise']}, preconditions=base_pre,
            depends_on=['camel-3-to-4'], finding_ids=ids(['CDI_SCOPE']), rollback=ROLLBACK_RECIPE,
            notes=['Solo el paquete CDI; en Spring Boot el paso de runtime convierte @ApplicationScoped en @Component (revisar).'])
    for f_rule in sorted(r for r in by_rule if r.startswith('JAKARTA_')):
        rule_recipe = {'JAKARTA_JMS': 'org.openrewrite.java.migrate.jakarta.JavaxJmsToJakartaJms',
                       'JAKARTA_PERSISTENCE': 'org.openrewrite.java.migrate.jakarta.JavaxPersistenceToJakartaPersistence',
                       'JAKARTA_SERVLET': 'org.openrewrite.java.migrate.jakarta.JavaxServletToJakartaServlet',
                       'JAKARTA_XMLBIND': 'org.openrewrite.java.migrate.jakarta.JavaxXmlBindMigrationToJakartaXmlBind',
                       'JAKARTA_VALIDATION': 'org.openrewrite.java.migrate.jakarta.JavaxValidationMigrationToJakartaValidation',
                       'JAKARTA_INJECT': 'org.openrewrite.java.migrate.jakarta.JavaxInjectMigrationToJakartaInject',
                       'JAKARTA_WS': 'org.openrewrite.java.migrate.jakarta.JavaxWsToJakartaWs',
                       'JAKARTA_ANNOTATION': 'org.openrewrite.java.migrate.jakarta.JavaxAnnotationMigrationToJakartaAnnotation'}[f_rule]
        add(f'jakarta-{f_rule[8:].lower()}', f'Jakarta {f_rule[8:].title()} (receta específica)', 'recipe', 'AUTO_TEST',
            recipe={'activeRecipes': [rule_recipe]}, preconditions=base_pre, depends_on=['camel-3-to-4'],
            finding_ids=ids([f_rule]), rollback=ROLLBACK_RECIPE,
            notes=['Solo el paquete indicado; javax.sql, javax.naming, javax.crypto y demás APIs Java SE permanecen.'])
    recipe, pre, notes, unmapped = runtime_recipe(profile, modules, routes, by_rule, scan.components or [], scan.route_builders or [],
                                                  renames={d['artifact']: d['target'] for d in decisions if d['action'] == 'RENAME'},
                                                  removed={d['artifact'] for d in decisions if d['action'] in ('REMOVE', 'BLOCK')})
    if java_version == '21':  # verified in rewrite-migrate-java 3.11.0 and 3.42.0
        recipe['activeRecipes'] = recipe['activeRecipes'] + ['org.openrewrite.java.migrate.UpgradeToJava21']
        notes.append('Java 21: org.openrewrite.java.migrate.UpgradeToJava21 (el perfil base usa Java 17).')
    add(f'runtime-{profile.runtime}', f'Runtime {profile.name}: BOM, starters/extensiones y arranque', 'recipe', 'REVIEW',
        recipe=recipe, preconditions=base_pre + [g1] + pre, depends_on=[mapping_dep], rollback=ROLLBACK_RECIPE,
        finding_ids=ids(['UNMAPPED_COMPONENT']), notes=notes)
    from .transform import plan_edits
    edits = plan_edits(endpoints, [{'ruleId': f.rule_id, 'file': f.file, 'line': f.line} for f in findings])
    scheme_edits = [e for e in edits if e['op'] == 'scheme']
    secret_edits = [e for e in edits if e['op'] in ('secret', 'uri-secret')]
    if scheme_edits:
        add('endpoints-config', 'Esquemas Camel retirados y URLs a propiedades', 'transform', 'REVIEW',
            recipe={'edits': scheme_edits}, preconditions=base_pre, depends_on=[f'runtime-{profile.runtime}'], rollback=ROLLBACK_RECIPE,
            finding_ids=ids(['HTTP4', 'QUARTZ2']),
            notes=['Ediciones localizadas (archivo, línea y literal exactos): http4/https4→http/https con URL en services.*.url, quartz2→quartz.'])
    if secret_edits:  # SECRET REMEDIATION runs after the recipes and does not depend on them
        add('secret-remediation', 'Remediación de secretos (valores → variables de entorno)', 'transform', 'REVIEW',
            recipe={'edits': secret_edits}, preconditions=base_pre, rollback=ROLLBACK_RECIPE, finding_ids=ids(['HARDCODED_SECRET']),
            notes=['Valores secretos de .properties/.yml reemplazados por ${MF_SECRET_*}; luego se vuelve a escanear el candidato.'])
    for rule, title in MANUAL_GROUPS.items():
        if by_rule.get(rule):
            add(f'manual-{rule.lower().replace("_", "-")}', title, 'manual', 'MANUAL', finding_ids=ids([rule]),
                notes=['Sin transformación automática. Bloquea el PR hasta que el hallazgo se resuelva o descarte con motivo.']
                + (RUNTIME_MIGRATION_PLAN if rule == 'TARGET_RUNTIME_MIGRATION_REQUIRED' else []))
    review = ids(sorted(BEHAVIOR_REVIEW))
    if review:
        add('behavior-review', 'Revisión de comportamiento (JMS, JNDI, descriptores JBoss)', 'manual', 'REVIEW', finding_ids=review,
            gate='G4', notes=['No bloquea recetas. Requiere evidencia de equivalencia: ack, redelivery, DLQ, duplicados, rollback BD.'])
    add('compile', 'Compilar candidato (mvn test-compile)', 'validation', 'AUTO', gate='G2')
    add('test', 'Pruebas unitarias del candidato (mvn test)', 'validation', 'AUTO', gate='G3')
    add('secret-scan', 'Búsqueda de secretos en el diff', 'validation', 'AUTO', gate='G5')
    add('equivalence', 'Evidencia de equivalencia (contratos, JMS, transacciones)', 'validation', 'MANUAL', gate='G4',
        notes=['Suite del proyecto ejecutada fuera de la herramienta; un revisor registra resultado y evidencia.'])
    add('diff-review', 'Revisión del diff por archivo', 'validation', 'MANUAL', gate='G6')
    return steps, unmapped


def compat_blocks(steps, modules) -> list[dict]:
    """BLOCK decisions of the Camel step with the POM location of the dependency (one finding each)."""
    camel = next((s for s in steps if s['key'] == 'camel-3-to-4'), None)
    out = []
    for d in ((camel or {}).get('recipe') or {}).get('compat', []):
        if d['action'] != 'BLOCK':
            continue
        loc = next(((m.path, dep['line']) for m in modules for dep in m.dependencies
                    if dep['groupId'] == 'org.apache.camel' and dep['artifactId'] == d['artifact']), ('pom.xml', 1))
        out.append(d | {'file': loc[0], 'line': loc[1] or 1, 'evidence': f"org.apache.camel:{d['artifact']} {d['code']}"})
    return out


def create_plan(db, scan: Scan, profile: TargetProfile, actor) -> Plan:
    from .models import Finding as F
    modules = list(db.scalars(select(Module).where(Module.scan_id == scan.id)))
    routes = list(db.scalars(select(Route).where(Route.scan_id == scan.id)))
    findings = list(db.scalars(select(F).where(F.scan_id == scan.id)))
    from .models import Endpoint
    by_route = {r.id: r for r in routes}
    endpoints = [{'component': e.component, 'file': by_route[e.route_id].file, 'line': e.line, 'dynamic': e.dynamic}
                 for e in db.scalars(select(Endpoint).where(Endpoint.route_id.in_(list(by_route))))] if routes else []
    steps, unmapped = build_steps(scan, profile, modules, routes, findings, endpoints=endpoints)
    blocks = compat_blocks(steps, modules)
    if blocks:  # doc 19: component without a valid Camel 4 target -> explicit blocking finding on the Camel step
        from .models import RuleCatalog
        rule = next(r for r in db.get(RuleCatalog, scan.catalog_id).rules if r['id'] == 'CAMEL_COMPONENT_COMPAT')
        for b in blocks:
            fp = f"CAMEL_COMPONENT_COMPAT:{b['file']}:{b['artifact']}:{profile.key}"
            if not db.scalar(select(F).where(F.scan_id == scan.id, F.fingerprint == fp)):
                db.add(F(scan_id=scan.id, project_id=scan.project_id, fingerprint=fp, rule_id=rule['id'], rule_version=rule['version'],
                         file=b['file'], line=b['line'], severity=rule['severity'], classification=rule['classification'],
                         blocking=True, recommendation=f"{b['code']}: {b['reason']}"[:4000], evidence=b['evidence'][:200]))
        db.flush()
        findings = list(db.scalars(select(F).where(F.scan_id == scan.id)))
        steps, unmapped = build_steps(scan, profile, modules, routes, findings, endpoints=endpoints)
    if unmapped:  # unsupported case -> explicit finding, which blocks the runtime step
        from .models import RuleCatalog
        rule = next(r for r in db.get(RuleCatalog, scan.catalog_id).rules if r['id'] == 'UNMAPPED_COMPONENT')
        for mod, d in unmapped:
            fp = f"UNMAPPED_COMPONENT:{mod.path}:{d['line']}:{profile.key}"
            if not db.scalar(select(F).where(F.scan_id == scan.id, F.fingerprint == fp)):
                db.add(F(scan_id=scan.id, project_id=scan.project_id, fingerprint=fp, rule_id=rule['id'], rule_version=rule['version'],
                         file=mod.path, line=d['line'], severity=rule['severity'], classification=rule['classification'],
                         blocking=True, recommendation=rule['recommendation'], evidence=f"{d['groupId']}:{d['artifactId']}"))
        db.flush()
        findings = list(db.scalars(select(F).where(F.scan_id == scan.id)))
        steps, _ = build_steps(scan, profile, modules, routes, findings, endpoints=endpoints)
    last = db.scalar(select(Plan).where(Plan.scan_id == scan.id).order_by(Plan.version.desc()))
    if last and last.status != 'superseded':
        last.status = 'superseded'
    digest = hashlib.sha256(json.dumps({'steps': steps, 'profile': profile.digest, 'scan': str(scan.id)}, sort_keys=True).encode()).hexdigest()
    plan = Plan(scan_id=scan.id, project_id=scan.project_id, profile_id=profile.id, version=(last.version + 1) if last else 1,
                digest=digest, created_by=actor)
    db.add(plan)
    db.flush()
    for i, s in enumerate(steps, 1):
        db.add(PlanStep(plan_id=plan.id, ordinal=i, **s))
    return plan


def evaluate(db, plan: Plan):
    """Steps of a stored plan with their current state (finding decisions read from the DB)."""
    steps = list(db.scalars(select(PlanStep).where(PlanStep.plan_id == plan.id).order_by(PlanStep.ordinal)))
    fids = {fid for s in steps for fid in s.finding_ids}
    status = {}
    if fids:
        import uuid
        for f in db.scalars(select(Finding).where(Finding.id.in_([uuid.UUID(x) for x in fids]))):
            status[str(f.id)] = (f.status, f.blocking)
    keys = ('key', 'title', 'kind', 'classification', 'gate', 'recipe', 'preconditions', 'finding_ids', 'depends_on', 'rollback', 'notes')
    return evaluate_steps([{k: getattr(s, k) for k in keys} | {'id': str(s.id), 'ordinal': s.ordinal} for s in steps], status)


def evaluate_steps(steps: list[dict], status: dict) -> list[dict]:
    """Pure evaluation: ready | blocked | cleared (manual, findings closed) | pending (manual, open non-blocking).
    status: finding id -> (status, blocking). Shared by the platform and the CLI."""
    out, state = [], {}
    for i, s in enumerate(steps, 1):
        reasons = [f"Precondición no cumplida: {p['description']} ({p['detail']})" for p in s['preconditions'] if not p['ok']]
        open_blocking = [fid for fid in s['finding_ids'] if status.get(fid, ('open', True))[0] not in CLOSED and status.get(fid, ('', True))[1]]
        if s['kind'] == 'manual':
            if open_blocking:
                st = 'blocked'
                reasons.append(f'{len(open_blocking)} hallazgo(s) bloqueante(s) sin resolver')
            elif any(status.get(fid, ('open',))[0] not in CLOSED for fid in s['finding_ids']):
                st = 'pending'
            else:
                st = 'cleared'
        else:
            if s['kind'] == 'recipe' and open_blocking:
                reasons.append(f'{len(open_blocking)} hallazgo(s) bloqueante(s) sin resolver')
            for dep in s['depends_on']:
                if state.get(dep) == 'blocked':
                    reasons.append(f'Depende de {dep}, que está bloqueado')
            st = 'blocked' if reasons else 'ready'
        state[s['key']] = st
        out.append({'id': s.get('id'), 'ordinal': s.get('ordinal', i), 'key': s['key'], 'title': s['title'], 'kind': s['kind'],
                    'classification': s['classification'], 'gate': s['gate'], 'state': st, 'blockedReasons': reasons,
                    'preconditions': s['preconditions'], 'findingIds': s['finding_ids'], 'dependsOn': s['depends_on'],
                    'recipe': s['recipe'], 'rollback': s['rollback'], 'notes': s['notes']})
    return out


def runnable_recipe_steps(evaluated):
    return [s for s in evaluated if s['kind'] in ('recipe', 'transform') and s['state'] == 'ready']


VALID_RECIPE = re.compile(r'^[A-Za-z][\w.]{2,200}$')

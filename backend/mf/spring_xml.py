"""The Spring XML context a JBoss WAR loads from web.xml (contextConfigLocation), carried over to Spring Boot.

On JBoss, ContextLoaderListener started the XML context: JMS connection factories, CXF endpoints, transaction policies and a
<camelContext>. A Spring Boot jar ignores WEB-INF, so the candidate compiled and passed its tests but did not start
(found with examples/jboss-pedidos-completo in Pruebas). This module, on the isolated candidate:

- moves each XML of contextConfigLocation from WEB-INF to src/main/resources/legacy/ and imports it with @ImportResource;
- removes what Spring Boot now provides: the JNDI lookup of the migrated datasource (spring.datasource.* plus the alias of
  MigratedDataSourceConfig), the CXF bus import (cxf-spring-boot-starter creates it), <context:property-placeholder> (its
  files go to spring.config.import, so ${...} in the XML and {{...}} in the routes resolve from the Spring environment) and
  the RouteBuilder beans that MigratedRoutesConfig already registers;
- repairs the Camel upgrade recipe, which wraps the <property> children of every <bean> in <properties> (valid for the Camel
  XML DSL beans, invalid for Spring beans);
- camel-cxf namespace http://camel.apache.org/schema/cxf -> .../cxf/jaxws (Camel 4);
- <camelContext>: Camel Spring Boot owns the CamelContext; its routes and rests go to src/main/resources/camel/*.xml (XML IO
  DSL, loaded by camel-xml-io-dsl-starter), with the attribute renames of Camel 3 (setHeader headerName -> name...).
  Anything else in it (onException, dataFormats, attributes besides id/streamCache) is reported as a manual action;
- CXFServlet url-pattern of web.xml -> cxf.path; activemq-client 5.x (javax.jms) -> the jakarta client Spring Boot manages.
Every change is reported; nothing is guessed: an element it does not understand stays and is listed for review."""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .security import safe_join

_PARAM = re.compile(r'<context-param>\s*<param-name>\s*contextConfigLocation\s*</param-name>\s*<param-value>\s*([^<]+?)\s*</param-value>', re.S)
_CXF_SERVLET = re.compile(r'<servlet>\s*<servlet-name>\s*([^<]+?)\s*</servlet-name>\s*<servlet-class>\s*org\.apache\.cxf\.transport\.servlet\.CXFServlet', re.S)
_MAPPING = re.compile(r'<servlet-mapping>\s*<servlet-name>\s*([^<]+?)\s*</servlet-name>\s*<url-pattern>\s*([^<]+?)\s*</url-pattern>', re.S)
_ROUTE_CHILDREN = ('route', 'rest', 'routeConfiguration', 'routeTemplate', 'restConfiguration')
_RENAMES = [(r'<(setHeader|removeHeader)(\s[^>]*?)\bheaderName=', r'<\1\2name='),
            (r'<(setProperty|removeProperty)(\s[^>]*?)\bpropertyName=', r'<\1\2name='),
            (r'<(setHeader|removeHeader)\s+headerName=', r'<\1 name='),
            (r'<(setProperty|removeProperty)\s+propertyName=', r'<\1 name=')]


def facts(web_xml: str) -> dict:
    """contextConfigLocation entries and the CXFServlet url-pattern of a web.xml."""
    m = _PARAM.search(web_xml)
    locations = [x for x in re.split(r'[,\s]+', m[1]) if x] if m else []
    cxf = set(_CXF_SERVLET.findall(web_xml))
    patterns = [p for n, p in _MAPPING.findall(web_xml) if n in cxf]
    return {'contextConfigLocation': locations, 'cxfServletPattern': patterns[0] if patterns else None}


def _source(prefix: str, location: str) -> str | None:
    if location.startswith('classpath:'):
        return f"{prefix}src/main/resources/{location[len('classpath:'):].lstrip('/')}"
    if location.startswith('/WEB-INF/') or location.startswith('WEB-INF/'):
        return f"{prefix}src/main/webapp/{location.lstrip('/')}"
    return None  # file:, wildcards: reported


def _children(body: str):
    """Top-level elements of an XML fragment: (name, text). Comments are kept with the element that follows them."""
    out, i, pending = [], 0, ''
    tag = re.compile(r'<(/?)([\w:.-]+)([^>]*?)(/?)>|<!--.*?-->', re.S)
    depth, start, name = 0, None, None
    for m in tag.finditer(body):
        if m[0].startswith('<!--'):
            if depth == 0:
                pending += body[i:m.end()] if start is None else ''
                i = m.end()
            continue
        closing, tname, selfclose = m[1] == '/', m[2], m[4] == '/'
        if depth == 0 and not closing:
            start, name = m.start(), tname
            if selfclose:
                out.append((name, pending + '\n' + body[start:m.end()]))
                pending, start, i = '', None, m.end()
                continue
            depth = 1
        elif not closing and not selfclose:
            depth += 1
        elif closing:
            depth -= 1
            if depth == 0:
                out.append((name, pending + '\n' + body[start:m.end()]))
                pending, start, i = '', None, m.end()
    return out


def migrate_xml(text: str, ds_beans: set[str], route_builders: set[str]) -> dict:
    """Spring XML text -> {'xml', 'routes' (XML IO DSL or None), 'imports' (property files), 'changes', 'manual', 'empty'}."""
    changes, manual, imports = [], [], []
    n = 0

    def unwrap(m):
        nonlocal n
        n += 1
        return m[1] + m[2] + m[3].strip() + '\n    ' + m[4]
    text = re.sub(r'(<bean\b[^>]*[^/]>)(\s*)<properties>(.*?)</properties>\s*(</bean>)', unwrap, text, flags=re.S)
    if n:
        changes.append(f'{n} <bean>: <properties> que agregó la receta de Camel quitado (Spring usa <property> directo)')
    if 'xmlns:cxf="http://camel.apache.org/schema/cxf"' in text:
        text = text.replace('xmlns:cxf="http://camel.apache.org/schema/cxf"', 'xmlns:cxf="http://camel.apache.org/schema/cxf/jaxws"')
        text = text.replace('http://camel.apache.org/schema/cxf http://camel.apache.org/schema/cxf/camel-cxf.xsd',
                            'http://camel.apache.org/schema/cxf/jaxws http://camel.apache.org/schema/cxf/jaxws/camel-cxf.xsd')
        changes.append('espacio de nombres camel-cxf → http://camel.apache.org/schema/cxf/jaxws (Camel 4)')
    text, k = re.subn(r'\n?[ \t]*<import\s+resource="classpath:META-INF/cxf/[\w.-]+\.xml"\s*/>', '', text)
    if k:
        changes.append('import de META-INF/cxf/*.xml quitado: el bus de CXF lo crea cxf-spring-boot-starter')
    for m in list(re.finditer(r'\n?[ \t]*<context:property-placeholder\b([^>]*)/>', text)):
        loc = re.search(r'\blocation\s*=\s*"([^"]+)"', m[1])
        if loc and all(x.startswith('classpath:') for x in loc[1].split(',')):
            imports += [x.strip() for x in loc[1].split(',')]
            text = text.replace(m[0], '')
            changes.append(f'<context:property-placeholder> → spring.config.import={loc[1]}')
        else:
            manual.append('<context:property-placeholder> sin location classpath: revisar')
    for m in list(re.finditer(r'\n?[ \t]*<jee:jndi-lookup\b([^>]*)/>', text)):
        bean = re.search(r'\bid\s*=\s*"([^"]+)"', m[1])
        if bean and bean[1] in ds_beans:
            text = text.replace(m[0], '')
            changes.append(f'<jee:jndi-lookup id="{bean[1]}"> quitado: spring.datasource.* con alias {bean[1]} (MigratedDataSourceConfig)')
        else:
            manual.append(f"<jee:jndi-lookup id=\"{bean[1] if bean else '?'}\">: JNDI no existe en Spring Boot, definir el bean")
    for m in list(re.finditer(r'\n?[ \t]*<bean\s+id="([\w.-]+)"\s+class="([\w.$]+)"\s*/>', text)):
        if m[2] in route_builders:
            text = text.replace(m[0], '')
            changes.append(f'bean {m[1]} ({m[2].rsplit(".", 1)[-1]}) quitado: lo registra MigratedRoutesConfig')
    routes = None
    ctx = re.search(r'\n?([ \t]*)<camelContext\b([^>]*)>(.*?)</camelContext>', text, re.S)
    if ctx:
        attrs = dict(re.findall(r'([\w:]+)\s*=\s*"([^"]*)"', ctx[2]))
        for a, v in attrs.items():
            if a in ('id', 'xmlns') or a.startswith('xmlns:'):
                continue
            if a == 'streamCache' and v == 'true':
                changes.append('streamCache="true": Camel 4 tiene stream caching activo por defecto')
            else:
                manual.append(f'<camelContext {a}="{v}">: pasar a camel.springboot.* / camel.main.* a mano')
        kept = []
        for name, frag in _children(ctx[3]):
            if name == 'routeBuilder':
                continue
            if name == 'propertyPlaceholder':
                loc = re.search(r'\blocation\s*=\s*"([^"]+)"', frag)
                if loc and all(x.strip().startswith('classpath:') for x in loc[1].split(',')):
                    imports += [x.strip() for x in loc[1].split(',') if x.strip() not in imports]
                    continue
                manual.append('<propertyPlaceholder> sin location classpath: revisar')
                continue
            if name in _ROUTE_CHILDREN:
                kept.append(frag)
            else:
                manual.append(f'<{name}> dentro de <camelContext>: no se traslada automáticamente (revisar)')
        if kept:
            body = ''.join(kept)
            for pat, rep in _RENAMES:
                body = re.sub(pat, rep, body)
            routes = '<?xml version="1.0" encoding="UTF-8"?>\n<camel>' + body.rstrip() + '\n</camel>\n'
            changes.append(f'{len(kept)} elemento(s) de <camelContext> (rutas/rest) → XML IO DSL de Camel 4')
        text = text.replace(ctx[0], '')
        changes.append('<camelContext> quitado: Camel Spring Boot crea el contexto (rutas Java vía MigratedRoutesConfig)')
    imports = list(dict.fromkeys(imports))
    body = re.sub(r'<!--.*?-->', '', re.search(r'<beans\b[^>]*>(.*)</beans>', text, re.S)[1] if '<beans' in text else text, flags=re.S)
    return {'xml': text, 'routes': routes, 'imports': imports, 'changes': changes, 'manual': manual, 'empty': not body.strip()}


def _drop_activemq_version(pom: str) -> tuple[str, bool]:
    def fix(m):
        block = m[0]
        v = re.search(r'<version>\s*([^<]+?)\s*</version>', block)
        return re.sub(r'\s*<version>[^<]*</version>', '', block, count=1) if v else block
    new = re.sub(r'<dependency>(?:(?!</dependency>).)*?<groupId>\s*org\.apache\.activemq\s*</groupId>\s*<artifactId>\s*activemq-client\s*</artifactId>'
                 r'(?:(?!</dependency>).)*?</dependency>', fix, pom, flags=re.S)
    return new, new != pom


def _add_dependency(pom: str, group: str, artifact: str) -> str:
    if f'<artifactId>{artifact}</artifactId>' in pom:
        return pom
    dep = f'<dependency><groupId>{group}</groupId><artifactId>{artifact}</artifactId></dependency>'
    m = None
    for m in re.finditer(r'</dependencies>', pom):  # the project's own list is the last one (dependencyManagement comes first)
        pass
    return pom[:m.start()] + '  ' + dep + '\n  ' + pom[m.start():] if m else pom


def _append_properties(root: Path, prefix: str, props: list[str]):
    path = safe_join(root, f'{prefix}src/main/resources/application.properties')
    old = path.read_text(encoding='utf-8') if path.is_file() else '# Generado por Migration Factory (contexto Spring XML de JBoss)\n'
    keys = {ln.split('=', 1)[0].strip() for ln in old.splitlines() if '=' in ln}
    add = [p for p in props if p.split('=', 1)[0] not in keys]
    if add:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(old.rstrip('\n') + '\n' + '\n'.join(add) + '\n', encoding='utf-8')


def apply(root: Path, e: dict) -> dict:
    """Edit 'spring-xml-context': e = {file (web.xml), prefix, package, dsBeans, routeBuilders}. Returns the result record."""
    r = {'op': e['op'], 'file': e['file'], 'line': 1, 'applied': False}
    prefix, changes, manual, imports, resources, route_files = e.get('prefix', ''), [], [], [], [], []
    web = safe_join(root, e['file'])
    if 'locations' in e:  # facts read at scan time: the runtime step of a WAR profile deletes web.xml before this step
        f = {'contextConfigLocation': e['locations'], 'cxfServletPattern': e.get('cxfServletPattern')}
    elif web.is_file():
        f = facts(web.read_text(encoding='utf-8'))
    else:
        return r | {'reason': 'web.xml no encontrado'}
    for loc in f['contextConfigLocation']:
        src = _source(prefix, loc)
        path = safe_join(root, src) if src else None
        if not path or not path.is_file():
            manual.append(f'contextConfigLocation {loc}: no se encontró el archivo')
            continue
        res = migrate_xml(path.read_text(encoding='utf-8'), set(e.get('dsBeans', [])), set(e.get('routeBuilders', [])))
        name = PurePosixPath(src).name
        changes += [f'{name}: {c}' for c in res['changes']]
        manual += [f'{name}: {c}' for c in res['manual']]
        imports += [i for i in res['imports'] if i not in imports]
        if res['routes']:
            out = safe_join(root, f'{prefix}src/main/resources/camel/{PurePosixPath(name).stem}-routes.xml')
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(res['routes'], encoding='utf-8')
            route_files.append(out.relative_to(root).as_posix())
        if res['empty']:
            changes.append(f'{name}: sin beans propios después de la migración; no se importa')
            if '/src/main/webapp/' in '/' + src:
                path.unlink()
            continue
        if '/src/main/webapp/' in '/' + src:
            dest = safe_join(root, f'{prefix}src/main/resources/legacy/{name}')
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(res['xml'], encoding='utf-8')
            path.unlink()
            resources.append(f'classpath:legacy/{name}')
            changes.append(f'{name}: WEB-INF → src/main/resources/legacy/ (@ImportResource)')
        else:
            path.write_text(res['xml'], encoding='utf-8')
            resources.append(loc)
    props = []
    if imports:
        props.append('spring.config.import=' + ','.join(imports))
    if f['cxfServletPattern'] and resources:
        cxf_path = f['cxfServletPattern'].rstrip('*').rstrip('/') or '/'
        props.append(f'cxf.path={cxf_path}')
        changes.append(f'CXFServlet {f["cxfServletPattern"]} → cxf.path={cxf_path}')
    if props:
        _append_properties(root, prefix, props)
    pom_path = safe_join(root, f'{prefix}pom.xml')
    if pom_path.is_file():
        pom = pom_path.read_text(encoding='utf-8')
        pom, dropped = _drop_activemq_version(pom)
        if dropped:
            changes.append('activemq-client: versión explícita quitada (5.x es javax.jms); usa la versión jakarta que gestiona Spring Boot')
        if route_files:
            pom = _add_dependency(pom, 'org.apache.camel.springboot', 'camel-xml-io-dsl-starter')
            changes.append('camel-xml-io-dsl-starter: carga ' + ', '.join(route_files) + ' (camel/*.xml por defecto)')
        pom_path.write_text(pom, encoding='utf-8')
    if resources and e.get('package'):
        pkg = e['package']
        cfg = safe_join(root, f"{prefix}src/main/java/{pkg.replace('.', '/')}/MigratedXmlContextConfig.java")
        listed = ', '.join(f'"{x}"' for x in resources)
        cfg.write_text(f'package {pkg};\n\nimport org.springframework.context.annotation.Configuration;\n'
                       'import org.springframework.context.annotation.ImportResource;\n\n'
                       '/** Generado por Migration Factory: el contexto Spring XML que JBoss cargaba desde web.xml (contextConfigLocation).\n'
                       ' *  JMS, CXF y transacciones siguen definidos ahí; revisar antes de aprobar. */\n'
                       f'@Configuration\n@ImportResource({{{listed}}})\npublic class MigratedXmlContextConfig {{\n}}\n', encoding='utf-8')
        changes.append('MigratedXmlContextConfig: @ImportResource(' + ', '.join(resources) + ')')
    elif resources:
        manual.append('Sin paquete Java para MigratedXmlContextConfig: agregar @ImportResource(' + ', '.join(resources) + ') a mano')
    if not changes:
        return r | {'reason': 'no había contexto Spring XML que trasladar', 'manual': manual}
    return r | {'applied': True, 'change': '; '.join(changes), 'changes': changes, 'manual': manual}

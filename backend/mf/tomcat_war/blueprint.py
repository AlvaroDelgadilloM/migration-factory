"""OSGi Blueprint XML -> Spring XML (text transformation that keeps comments, order and formatting of the original).

Only the wiring changes. Bean definitions, CXF endpoints and Camel routes are kept; what has no Spring equivalent is
mapped onto the compatibility layer:
  <reference>/<service>      -> ServiceImport / ServiceExport (exact interface name + component name, as in OSGi)
  ext:property-placeholder   -> one BridgePropertyPlaceholderConfigurer per module (${..} and {{..}}) + one configurer per custom prefix
  <xmljson id=".."/>         -> XmlJsonDataFormat bean with the same id and options
  OsgiServletRegisterer      -> META-INF/web-fragment.xml (see `servlet_fragment`)
Anything not covered is reported as MANUAL; nothing is guessed.
"""
import re

NL = '\r\n'
_PLACEHOLDER = re.compile(r'<ext:property-placeholder\b([^>]*?)(/>|>(.*?)</ext:property-placeholder>)', re.S)
_LOCATION = re.compile(r'<ext:location\s*>(.*?)</ext:location>', re.S)
_REFERENCE = re.compile(r'<reference\b([^>]*?)(/>|>\s*</reference>)', re.S)
_SERVICE = re.compile(r'<service\b([^>]*?)(/>|>\s*</service>)', re.S)
_XMLJSON = re.compile(r'<xmljson\b([^>]*?)(/>|>\s*</xmljson>)', re.S)
_ROUTE_DESCRIPTION = re.compile(r'<route\b([^>]*)>(\s*)<description>(.*?)</description>', re.S)
_ROOT = re.compile(r'<blueprint\b([^>]*)>', re.S)
_SERVLET_BEAN = re.compile(r'<bean\b[^>]*OsgiServletRegisterer[^>]*>(.*?)</bean>', re.S)
_KEPT_NAMESPACES = re.compile(r'osgi\.org|aries\.apache\.org|camel\.apache\.org|XMLSchema-instance|cxf\.apache\.org', re.I)
UNSUPPORTED = re.compile(r'<cm:|<argument\b|activation=|<reference-list', re.I)


def attr(tag: str, name: str):
    m = re.search(r'(?<![\w:-])' + re.escape(name) + r'\s*=\s*(["\'])(.*?)\1', tag, re.S)
    return m.group(2) if m else None


def is_servlet_registration(text: str) -> bool:
    return 'osgiservletregisterer' in text.lower()


def servlet_fragment(text: str, source_name: str, artifact_id: str) -> tuple[str, int]:
    """Servlets registered through the OSGi HttpService -> web-fragment.xml with the same servlet names and aliases
    (Tomcat registers them when it finds the jar in WEB-INF/lib; restConfiguration() in the routes does not change)."""
    out = ('<?xml version="1.0" encoding="UTF-8"?>' + NL
           + f'<!-- GENERADO por Migration Factory a partir de {source_name} (OsgiServletRegisterer) -->' + NL
           + '<web-fragment xmlns="https://jakarta.ee/xml/ns/jakartaee" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"' + NL
           + '\txsi:schemaLocation="https://jakarta.ee/xml/ns/jakartaee https://jakarta.ee/xml/ns/jakartaee/web-fragment_6_0.xsd" version="6.0">' + NL
           + f"\t<name>{re.sub(r'[^A-Za-z0-9]', '_', artifact_id)}</name>" + NL)
    n = 0
    for m in _SERVLET_BEAN.finditer(text):
        alias = re.search(r'name="alias"\s+value="([^"]+)"', m.group(1))
        name = re.search(r'name="servletName"\s+value="([^"]+)"', m.group(1))
        if not alias or not name:
            continue
        out += (f'\t<servlet>{NL}\t\t<servlet-name>{name.group(1)}</servlet-name>{NL}'
                f'\t\t<servlet-class>org.apache.camel.component.servlet.CamelHttpTransportServlet</servlet-class>{NL}'
                f'\t\t<load-on-startup>1</load-on-startup>{NL}\t</servlet>{NL}\t<servlet-mapping>{NL}'
                f'\t\t<servlet-name>{name.group(1)}</servlet-name>{NL}\t\t<url-pattern>{alias.group(1)}/*</url-pattern>{NL}\t</servlet-mapping>{NL}')
        n += 1
    return out + '</web-fragment>' + NL, n


def convert(text: str, file: str, report: list, compat: str, etc_property: str = 'mf.etc', camel2: bool = False) -> str:
    """text: Blueprint XML (decoded 1:1, e.g. latin-1). report: list of human-readable lines (appended).
    compat: package of the compatibility layer. etc_property: system property that points to the external
    configuration directory (Karaf resolved `file:etc/...` against its installation directory).
    camel2=True converts ONLY the wiring and keeps everything else for Camel 2 + Spring: it is how the verification
    baseline runs the original code outside Karaf (mf.tomcat_war.verify); never used for the migrated output."""
    etc = '${' + etc_property + '}'

    def location(loc):
        return re.sub(r'^file:etc/', lambda _: f'file:{etc}/', loc.strip(), flags=re.I)

    def values(locs):
        return ''.join(f'\t\t\t\t<value>{loc}</value>{NL}' for loc in locs)

    # --- property placeholders ------------------------------------------------------------------------------------
    all_locations = [location(loc) for m in _PLACEHOLDER.finditer(text) for loc in _LOCATION.findall(m.group(3) or '')]
    state = {'first': True}

    def placeholder(m):
        prefix, suffix = attr(m.group(1), 'placeholder-prefix'), attr(m.group(1), 'placeholder-suffix')
        locs = [location(loc) for loc in _LOCATION.findall(m.group(3) or '')]
        out = ''
        if state['first']:  # one bridge per module: resolves Spring ${...} and Camel {{...}} with ALL the files
            state['first'] = False
            out += ('<bean id="camelProperties" class="org.apache.camel.spring.spi.BridgePropertyPlaceholderConfigurer">' + NL
                    + f'\t\t<property name="locations">{NL}\t\t\t<list>{NL}' + values(all_locations) + f'\t\t\t</list>{NL}\t\t</property>{NL}'
                    + f'\t\t<property name="ignoreUnresolvablePlaceholders" value="true" />{NL}\t</bean>')
        if prefix:
            if out:
                out += NL + '\t'
            out += ('<bean class="org.springframework.context.support.PropertySourcesPlaceholderConfigurer">' + NL
                    + f'\t\t<property name="locations">{NL}\t\t\t<list>{NL}' + values(locs) + f'\t\t\t</list>{NL}\t\t</property>{NL}'
                    + f'\t\t<property name="placeholderPrefix" value="{prefix}" />{NL}')
            if suffix:
                out += f'\t\t<property name="placeholderSuffix" value="{suffix}" />{NL}'
            out += f'\t\t<property name="ignoreUnresolvablePlaceholders" value="true" />{NL}\t</bean>'
        return out or '<!-- property-placeholder: incluido en camelProperties -->'

    text = _PLACEHOLDER.sub(placeholder, text)
    if all_locations:
        report.append(f'  [xml] {file} : {len(all_locations)} property-placeholder -> Spring')
    text = re.sub(r'(location\s*=\s*"[^"]*?)file:etc/', lambda m: f'{m.group(1)}file:{etc}/', text, flags=re.I)

    # --- <reference> -> ServiceImport ------------------------------------------------------------------------------
    def reference(m):
        a = m.group(1)
        rid, itf, cn = attr(a, 'id'), attr(a, 'interface'), attr(a, 'component-name')
        if itf == 'org.osgi.service.http.HttpService':
            return f'<!-- reference {rid} (HttpService de OSGi): en Tomcat los servlets se declaran en web.xml -->'
        out = f'<bean id="{rid}" class="{compat}.osgi.ServiceImport">{NL}\t\t<property name="interfaceName" value="{itf}" />{NL}'
        if cn:
            out += f'\t\t<property name="componentName" value="{cn}" />{NL}'
        return out + '\t</bean>'

    n = len(_REFERENCE.findall(text))
    text = _REFERENCE.sub(reference, text)
    if n:
        report.append(f'  [xml] {file} : {n} <reference> -> ServiceImport')

    # --- <service> -> ServiceExport --------------------------------------------------------------------------------
    def service(m):
        a = m.group(1)
        sid, itf, ref = attr(a, 'id'), attr(a, 'interface'), attr(a, 'ref')
        return (f'<bean id="{sid}" class="{compat}.osgi.ServiceExport">{NL}\t\t<property name="ref" ref="{ref}" />{NL}'
                f'\t\t<property name="interfaceName" value="{itf}" />{NL}\t\t<property name="componentName" value="{ref}" />{NL}\t</bean>')

    n = len(_SERVICE.findall(text))
    text = _SERVICE.sub(service, text)
    if n:
        report.append(f'  [xml] {file} : {n} <service> -> ServiceExport')
    if re.search(r'<service\b', text, re.I):
        report.append(f'  [MANUAL] {file} : <service> con contenido anidado, revisar')

    # --- <xmljson> -> compatibility data format bean -----------------------------------------------------------------
    beans = ''
    for m in ([] if camel2 else _XMLJSON.finditer(text)):
        beans += f'\t<bean id="{attr(m.group(1), "id")}" class="{compat}.xmljson.XmlJsonDataFormat">{NL}'
        for k, v in re.findall(r'(\w+)\s*=\s*"([^"]*)"', m.group(1), re.S):
            if k.lower() != 'id':
                beans += f'\t\t<property name="{k}" value="{v}" />{NL}'
        beans += f'\t</bean>{NL}'
    if beans:
        n = len(_XMLJSON.findall(text))
        text = _XMLJSON.sub('', text)
        text = re.sub(r'\s*<dataFormats>(\s|<!--(?:(?!-->).)*-->)*</dataFormats>', '', text, flags=re.S)
        text = re.sub(r'([ \t]*)(<camelContext\b)', lambda m: f'{beans}{NL}{m.group(1)}{m.group(2)}', text, count=1, flags=re.I)
        report.append(f'  [xml] {file} : {n} <xmljson> -> bean XmlJsonDataFormat')

    # --- <route><description> -> attribute (the Camel 4 XSD no longer allows the element) -----------------------------
    def description(m):
        d = re.sub(r'\s+', ' ', m.group(3)).strip().replace('"', '&quot;')
        return f'<route{m.group(1)} description="{d}">'

    n = 0 if camel2 else len(_ROUTE_DESCRIPTION.findall(text))
    if n:
        text = _ROUTE_DESCRIPTION.sub(description, text)
        report.append(f'  [xml] {file} : {n} <description> de ruta -> atributo')

    # --- Camel XML DSL: renamed attributes/elements -------------------------------------------------------------------
    before = text
    if not camel2:
        text = re.sub(r'(<(?:setHeader|removeHeader)\b[^>]*?)\bheaderName=', r'\1name=', text)
        text = re.sub(r'(<(?:setProperty|removeProperty)\b[^>]*?)\bpropertyName=', r'\1name=', text)
        text = re.sub(r'<(marshal|unmarshal)\b([^>]*?)\s+ref="([^"]+)"([^>]*?)/>', r'<\1\2\4><custom ref="\3"/></\1>', text)
        text = re.sub(r'quartz2:', 'quartz:', text, flags=re.I)
    seen = {}

    def unique_id(m):  # editor-generated ids (_to5, _log1...) repeated: Camel 4 requires unique ids
        i = m.group(1)
        if i in seen:
            seen[i] += 1
            return f' id="{i}_{seen[i]}"'
        seen[i] = 1
        return m.group(0)

    if not camel2:
        text = re.sub(r' id="(_\w+)"', unique_id, text)
    if text != before:
        report.append(f'  [xml] {file} : DSL XML de Camel ajustado (headerName/propertyName/marshal ref/quartz2)')

    # --- syntax -------------------------------------------------------------------------------------------------------
    text = re.sub(r'component-id\s*=', 'bean=', text, flags=re.I)
    text = re.sub(r'xmlns="http://camel\.apache\.org/schema/blueprint"', 'xmlns="http://camel.apache.org/schema/spring"', text, flags=re.I)
    if not camel2:
        text = re.sub(r'org\.apache\.activemq\.camel\.component\.ActiveMQComponent', 'org.apache.camel.component.activemq.ActiveMQComponent', text, flags=re.I)
    if is_servlet_registration(text):
        report.append(f'  [MANUAL] {file} : OsgiServletRegisterer -> declarar los servlets en web.xml')
    if UNSUPPORTED.search(text):
        report.append(f'  [MANUAL] {file} : sintaxis Blueprint no cubierta (cm:/argument/activation/reference-list)')

    # --- root element -------------------------------------------------------------------------------------------------
    root = _ROOT.search(text)
    if not root:
        report.append(f'  [MANUAL] {file} : no es un documento <blueprint>; se copia sin convertir')
        return text
    custom = ''.join(f'{NL}\txmlns:{p}="{uri}"' for p, uri in re.findall(r'xmlns:([\w-]+)\s*=\s*"([^"]+)"', root.group(1))
                     if not _KEPT_NAMESPACES.search(uri))
    schemas = ('http://www.springframework.org/schema/beans http://www.springframework.org/schema/beans/spring-beans.xsd'
               + NL + '\t\thttp://camel.apache.org/schema/spring http://camel.apache.org/schema/spring/camel-spring.xsd')
    ns = ''
    # the prefix the file used for camel-cxf (cxf, camel-cxf, ...) is kept
    cxf_prefixes = re.findall(r'xmlns:([\w-]+)\s*=\s*"http://camel\.apache\.org/schema/blueprint/cxf"', root.group(1))
    if not cxf_prefixes and re.search(r'<cxf:', text, re.I):
        cxf_prefixes = ['cxf']
    cxf_ns = 'http://camel.apache.org/schema/cxf' if camel2 else 'http://camel.apache.org/schema/cxf/jaxws'
    for p in cxf_prefixes:
        ns += f'{NL}\txmlns:{p}="{cxf_ns}"'
    if cxf_prefixes:
        schemas += NL + f'\t\t{cxf_ns} {cxf_ns}/camel-cxf.xsd'
    if re.search(r'<http-conf:', text, re.I):
        ns += f'{NL}\txmlns:http-conf="http://cxf.apache.org/transports/http/configuration"'
        schemas += NL + '\t\thttp://cxf.apache.org/transports/http/configuration http://cxf.apache.org/schemas/configuration/http-conf.xsd'
    new_root = (f'<beans xmlns="http://www.springframework.org/schema/beans"{NL}\txmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
                f'{ns}{custom}{NL}\txsi:schemaLocation="{schemas}">')
    text = text[:root.start()] + new_root + text[root.end():]
    return re.sub(r'</blueprint>', '</beans>', text, flags=re.I)


def unresolved_imports(module_xml: dict) -> list[dict]:
    """module_xml: module -> [converted XML texts]. ServiceImport beans that no module exports: in the source system those
    services exist only in what is deployed (a newer bundle than the repository), so they are reported, never invented."""
    exports = set()
    for texts in module_xml.values():
        for t in texts:
            for itf, name in re.findall(r'ServiceExport">.*?name="interfaceName" value="([^"]+)".*?name="componentName" value="([^"]+)"', t, re.S):
                exports |= {(itf, name), (itf, '')}
    out = []
    for module, texts in sorted(module_xml.items()):
        for t in texts:
            for bean, itf, name in re.findall(r'<bean id="([^"]+)" class="[\w.]+\.osgi\.ServiceImport">\s*<property name="interfaceName" value="([^"]+)" />'
                                              r'\s*(?:<property name="componentName" value="([^"]+)" />)?', t, re.S):
                if (itf, name) not in exports:
                    out.append({'module': module, 'bean': bean, 'interface': itf, 'componentName': name or None})
    return out

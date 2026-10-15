"""Facts of the legacy JBoss deployment that the Spring Boot runtime needs, read from the repository itself (never guessed):

  jndiDataSources  <jee:jndi-lookup id=".." jndi-name="java:..."/> in Spring XML: the bean name the routes use (#id)
  jdbcDrivers      JDBC driver coordinates the repository mentions (pom dependency/plugin config, *-ds.xml, module.xml):
                   on JBoss the server provided the driver; a Spring Boot jar must carry it
  servletPatterns  url-pattern of CamelHttpTransportServlet in web.xml (the REST URLs clients call today)
  contextRoots     jboss-web.xml <context-root>, else the WAR <finalName>: JBoss served the app under /<that>
Stdlib only (the scanner also runs without the platform)."""
from __future__ import annotations

import re

DRIVERS = {  # groupId:artifactId of known JDBC drivers -> database
    'org.postgresql:postgresql': 'postgresql', 'com.oracle.database.jdbc:ojdbc8': 'oracle', 'com.oracle.database.jdbc:ojdbc11': 'oracle',
    'com.oracle:ojdbc6': 'oracle', 'com.oracle:ojdbc7': 'oracle', 'com.oracle:ojdbc8': 'oracle', 'mysql:mysql-connector-java': 'mysql',
    'com.mysql:mysql-connector-j': 'mysql', 'org.mariadb.jdbc:mariadb-java-client': 'mariadb',
    'com.microsoft.sqlserver:mssql-jdbc': 'sqlserver', 'com.h2database:h2': 'h2',
}
_JNDI = re.compile(r'<jee:jndi-lookup\b([^>]*)/?>', re.S)
_ATTR = lambda name: re.compile(rf'\b{name}\s*=\s*"([^"]+)"')
_COORD = re.compile(r'<groupId>\s*([\w.\-]+)\s*</groupId>\s*<artifactId>\s*([\w.\-]+)\s*</artifactId>\s*(?:<version>\s*([\w.\-]+)\s*</version>)?')
_SERVLET = re.compile(r'<servlet>\s*<servlet-name>\s*([^<]+?)\s*</servlet-name>\s*<servlet-class>\s*org\.apache\.camel\.component\.servlet\.CamelHttpTransportServlet', re.S)
_MAPPING = re.compile(r'<servlet-mapping>\s*<servlet-name>\s*([^<]+?)\s*</servlet-name>\s*<url-pattern>\s*([^<]+?)\s*</url-pattern>', re.S)


def empty() -> dict:
    return {'jndiDataSources': [], 'jdbcDrivers': [], 'servletPatterns': [], 'contextRoots': []}


def collect(rel: str, text: str, facts: dict) -> None:
    if not rel.endswith('.xml'):
        return
    for m in _JNDI.finditer(text):
        bean, jndi = _ATTR('id').search(m[1]), _ATTR('jndi-name').search(m[1])
        if bean and jndi and re.search(r'datasource|jdbc|ds$', (jndi[1] + ' ' + bean[1]).lower()):
            facts['jndiDataSources'].append({'bean': bean[1], 'jndiName': jndi[1], 'file': rel})
    for g, a, v in _COORD.findall(text):
        if f'{g}:{a}' in DRIVERS and not any(d['artifact'] == f'{g}:{a}' for d in facts['jdbcDrivers']):
            facts['jdbcDrivers'].append({'artifact': f'{g}:{a}', 'version': v or None, 'database': DRIVERS[f'{g}:{a}'], 'file': rel})
    if rel.endswith('web.xml'):
        camel = set(_SERVLET.findall(text))
        for name, pattern in _MAPPING.findall(text):
            if name in camel:
                facts['servletPatterns'].append({'pattern': pattern, 'file': rel})
    if rel.endswith('jboss-web.xml'):
        m = re.search(r'<context-root>\s*/?([^<\s]*)\s*</context-root>', text)
        if m:
            facts['contextRoots'].append({'path': '/' + m[1].strip('/'), 'source': 'jboss-web.xml', 'file': rel})
    if rel.endswith('pom.xml') and re.search(r'<packaging>\s*war\s*</packaging>', text):
        m = re.search(r'<build>.*?<finalName>\s*([\w.\-]+)\s*</finalName>', text, re.S)
        if m:
            facts['contextRoots'].append({'path': '/' + m[1], 'source': 'finalName', 'file': rel})


if __name__ == '__main__':  # self-check with the shapes of examples/jboss-legacy-shop
    f = empty()
    collect('app/src/main/webapp/WEB-INF/applicationContext.xml',
            '<jee:jndi-lookup id="shopDS" jndi-name="java:jboss/datasources/ShopDS"/>', f)
    collect('app/src/main/webapp/WEB-INF/web.xml', '<servlet>\n <servlet-name>CamelServlet</servlet-name>\n <servlet-class>'
            'org.apache.camel.component.servlet.CamelHttpTransportServlet</servlet-class></servlet><servlet-mapping><servlet-name>'
            'CamelServlet</servlet-name><url-pattern>/api/*</url-pattern></servlet-mapping>', f)
    collect('app/pom.xml', '<packaging>war</packaging><build><finalName>shop</finalName><plugins><artifactItem><groupId>org.postgresql'
            '</groupId><artifactId>postgresql</artifactId><version>42.2.27</version></artifactItem></plugins></build>', f)
    assert f['jndiDataSources'] == [{'bean': 'shopDS', 'jndiName': 'java:jboss/datasources/ShopDS', 'file': 'app/src/main/webapp/WEB-INF/applicationContext.xml'}]
    assert f['servletPatterns'][0]['pattern'] == '/api/*' and f['contextRoots'][0]['path'] == '/shop'
    assert f['jdbcDrivers'][0] == {'artifact': 'org.postgresql:postgresql', 'version': '42.2.27', 'database': 'postgresql', 'file': 'app/pom.xml'}
    print('ok')

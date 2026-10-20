# Perfil `tomcat-war`: Fuse/Karaf (Camel 2 + Blueprint) → WAR para Tomcat (Camel 4 + Spring)

Para aplicaciones que hoy son **bundles OSGi en JBoss Fuse/Karaf** y cuyo dueño pide cambiar de plataforma **sin
rediseñar**. Los perfiles Spring Boot y Quarkus reescriben el arranque y la configuración; este no.

| | Spring Boot / Quarkus | `tomcat-war` |
|---|---|---|
| Origen típico | WAR/EAR en JBoss EAP | bundles OSGi + Blueprint en Fuse/Karaf |
| Resultado | una aplicación por proyecto | **un WAR** con todos los bundles del workspace |
| Rutas, procesadores, XSLT, WSDL, SQL | se adaptan | **no se tocan** |
| `direct-vm`, `xmljson`, OSGi services | bloqueo manual | capa de compatibilidad |
| Camel 2 → 4 | decisión manual + receta 3→4 | reglas declarativas (cambios de una línea) |
| Dónde corre | CLI y plataforma | CLI y plataforma (migración de workspace con destino «Tomcat WAR») |

## Uso

```bash
python migrate.py plan    <workspace> --target tomcat-war --output <salida>              # solo reportes
python migrate.py migrate <workspace> --target tomcat-war --output <salida> \
       [--config cliente.json] [--build] [--fail-on-critical]
```

Salida: `migrated-workspace/` (y `.zip`), `reports/tomcat-war-report.{md,json}`, `logs/build.log` con `--build`.
Estado `SUCCEEDED` o `PARTIAL_SUCCESS` (quedan puntos de revisión manual o servicios sin exportador).
El origen solo se lee; la salida no puede estar dentro de él.

```text
migrated-workspace/
  pom.xml                 padre: versiones del perfil (Camel, Spring, CXF, Tomcat)
  compat/                 capa de compatibilidad (código nuevo, 15 clases)
  modules/<bundle>/       cada bundle como jar; sus XML en META-INF/modules/<bundle>/
  webapp/                 el WAR: web.xml, contexto raíz
```

Para levantarlo: `mvn -Prun -DskipTests verify -Dmf.etc=<carpeta etc> [-Dmf.container=<nombre>]` (Tomcat real vía Cargo).

## Qué hace el motor (`backend/mf/tomcat_war`)

1. **Blueprint → Spring XML** (`blueprint.py`). Solo cambia el cableado: `<reference>`/`<service>` → `ServiceImport`/`ServiceExport`
   (misma semántica de OSGi: interfaz exacta + nombre del componente), `ext:property-placeholder` → un puente por módulo,
   `<xmljson>` → bean con el mismo id y opciones, `file:etc/...` → `file:${mf.etc}/...`. Se conservan comentarios y formato.
2. **Reglas Java** (`rules/tomcat-war/camel2-to-4.json`). Cada regla es un patrón y un reemplazo de una línea, con nivel
   AUTO / AUTO_TEST / REVIEW. Incluye lo que Camel 2 toleraba y Camel 4 rechaza al arrancar.
3. **POM por módulo**: `bundle` → `jar`, dependencias Camel 4 según lo que el módulo usa. Todo artefacto `org.apache.camel`
   se verifica contra el snapshot del BOM (`rules/camel/boms/camel-bom-4.18.4.json`); si no está, la migración se detiene.
4. **Servlets OSGi** (`OsgiServletRegisterer`) → `META-INF/web-fragment.xml` con el mismo nombre y alias.
5. **Reportes**: cambios por archivo, puntos de revisión manual y **servicios importados que nadie exporta**.

## Capa de compatibilidad (`rules/tomcat-war/template/compat`)

| Paquete | Sustituye a |
|---|---|
| `directvm` | componente `direct-vm`, eliminado en Camel 4 |
| `xmljson` | dataformat `xmljson`, eliminado en Camel 3 (misma librería json-lib: mismo JSON) |
| `xslt` | opción `saxon=true` del componente `xslt` |
| `osgi` | registro de servicios OSGi |
| `spring` | un contexto Spring y un CamelContext por módulo (aislamiento de cada bundle); filtro por contenedor |
| `servlet` | Tomcat entrega los headers HTTP en minúsculas: se restaura el nombre que esperan las rutas. Una petición con `Content-Length: 0` llega con cuerpo `null`, como en Camel 2 (`LegacyHttpBinding`; Camel 4 entrega un cuerpo vacío) |
| `cxf` | `setFaultBody` (se lanza el fault guardado) |
| `sql` | consulta `SelectOne` sin filas: se conserva el cuerpo del mensaje, como en Camel 2 (Camel 4 lo deja en null y la ruta toma otra rama sin dar error) |
| `spring` (`LegacyCamelSettings`) | `Content-Type` de una respuesta REST que la ruta no fija: primero el `produces` del servicio, como en Camel 2 (Camel 4 mira primero el `Accept`) |
| `spring` (`LegacyCamelSettings`) | *stream caching* apagado por omisión, como en Camel 2 (encendido desde Camel 3): con él, una ruta que no fija respuesta devolvía el cuerpo de la petición en lugar de vacío. Se respeta `streamCache="true"` del XML y de cada ruta |
| `spring` (`LegacyCamelSettings.CxfOperationHeaders`) | nombre de la operación SOAP: Camel 2 usaba los headers `operationName`/`operationNamespace` y Camel 3+ `CamelCxfOperationName`/`CamelCxfOperationNamespace`. Se mantienen los dos: al entrar por `cxf:` se copian los de Camel 4 a los de Camel 2, y antes de cada llamada a `cxf:` al revés. Sin esto `toD("direct:${header.operationName}")` recibe vacío y una llamada con `setHeader("operationName", X)` no elige la operación X |
| `osgi` (`TestDataSources`) | solo en pruebas automáticas (`-Dmf.test.jdbcUrl`): un datasource que publica otro bundle del servidor se resuelve con la base de prueba |

Las filas `sql`, `servlet` y `spring` salieron de las pruebas de respuestas: son cambios de Camel 4 que no dan
error al compilar ni al arrancar. Lo mismo la regla Java `SIMPLE_WITHOUT_PLACEHOLDER`: Camel 2 evaluaba `simple("null")`,
`simple("body")`, `simple("header.x")` o `simple("exception.message")` (sin `${}`) como la función y Camel 4 los toma como
texto literal (el cuerpo quedaba en la palabra "null"); se verificó evaluando las mismas expresiones con camel-core 2.23.2 y
4.18.4. El conversor de XML aplica lo mismo a `<simple>null</simple>`.

Es código que el equipo debe mantener. Pruebas JUnit en `compat/src/test` (direct-vm entre contextos, registro OSGi, xmljson, nombres de headers): viajan con el workspace migrado (`mvn -pl compat test`) y el CI las corre en cada push (job `tomcat-compat`).

## Archivo del cliente (`--config`)

Las decisiones de un cliente no viven en el motor:

```json
{
  "groupId": "com.cliente.migrated", "version": "1.0.0-SNAPSHOT",
  "containers": {"names": ["mobile", "web1"], "file": ".gitlab-ci.yml", "skipPattern": "SKIP_{NAME}:\\s*\"?true"},
  "patches": [{"name": "motivo", "module": "artifactId", "file": "Clase.java", "find": "...", "replace": "...", "regex": false}],
  "extraWarDependencies": ["g:a:v"],
  "overlay": "carpeta-con-archivos-extra"
}
```

- `containers`: si varios contenedores de origen despliegan subconjuntos distintos de bundles, cada módulo guarda dónde va
  y el WAR arranca con `-Dmf.container=<nombre>`. Hay bundles alternativos entre sí (mismas rutas REST) que no pueden convivir.
- `patches`: ajustes que no son mecánicos. **Si el texto ya no coincide, la migración se detiene**: nunca se aplican a ciegas.
- `overlay`: archivos que se copian encima del resultado (p. ej. datasources del ambiente destino).

## Evidencia de equivalencia (gate G4): `migrate.py verify`

Compilar y arrancar prueban poco. Camel 4 arma distinto algunos bloques **sin dar error**: `.endChoice()` en un `choice`
anidado, `.end()` después de `validate()`, `.end()` dentro de un `otherwise()`. Dos comprobaciones comparan el código
migrado contra el **original corriendo en su versión de Camel**, sin acceso a los servidores del cliente
(`backend/mf/tomcat_war/verify.py`; necesitan JDK 17+, Maven y el workspace ya compilado con `--build`).

```bash
# 1. Estructura de las rutas (no levanta nada)
python migrate.py verify routes <workspace> --migrated <salida>/migrated-workspace [--config cliente.json] --output <salida> [--fix]

# 2. Respuestas: misma petición al original y al migrado
python migrate.py verify baseline-server <workspace> --modules a,b,c --servlets Nombre=/alias --etc <etc> \
       [--classpath <jars extra>] [--port 18081] --output <salida>      # primer plano: código ORIGINAL + Camel 2 + Jetty
python migrate.py verify responses --old http://localhost:18081 --new http://localhost:18080 --cases casos.json --output <salida>
```

**`routes`** compila los fuentes originales contra las librerías de Camel 2 del origen, ejecuta `configure()` de cada
`RouteBuilder` en las dos versiones y compara el árbol resultante (el nivel de anidamiento es parte de la comparación).
Las rutas definidas en XML (`<route>` dentro de `<camelContext>` o `<routeContext>` de los Blueprint/Spring) se comparan igual:
los `<route>` del Blueprint original los carga el lector XML de Camel 2 y los del XML convertido el de Camel 4, y se vuelcan
con el mismo formato (clave `archivo.xml | from`). Las rutas comentadas no cuentan; un `<prefijo:route>` se reporta como no
comparado.

**`--fix`** (la plataforma lo aplica siempre): Camel 2 y Camel 4 cierran distinto algunos `.end()`. Por ejemplo, un `.end()`
después de un `filter` dentro de una rama de `choice` regresa a la rama en Camel 2 y al `choice` en Camel 4. Por cada ruta
distinta se prueba quitar cada `.end()`/`.endChoice()`, de uno en uno; se recompila la clase y se vuelca otra vez. Se acepta
solo si todas las rutas de la clase quedan idénticas a Camel 2. El cambio se escribe en el workspace migrado, se recompila el
WAR y queda como «corregida automáticamente (revisar)»: conserva lo que corre hoy, que puede ser un error del original.
`rules/tomcat-war/route-equivalences.json` lista lo que no cuenta como diferencia: lo que el motor cambia a propósito y lo
que cada versión solo imprime distinto, cada entrada con su motivo. Una ruta distinta en una clase con parche del cliente
se reporta como «por parche documentado»; cualquier otra es `FAIL`. Límite: no compara `<rest>` definidos en XML.

**`responses`** manda cada caso de `casos.json` a los dos sistemas y compara código HTTP, `Content-Type` y cuerpo (de una
traza de excepción, solo la primera línea). Una diferencia revisada se registra en el caso con `"accept": "motivo"`: sigue
apareciendo en el reporte, como aceptada. La línea base no es Karaf: los beans se cablean con Spring; rutas, procesadores,
XSLT, WSDL y versión de Camel son los originales.

Cada diferencia lleva su tipo, que no cambia el resultado: `ERROR_TEXT` (los dos sistemas fallan con el mismo código HTTP y
cambia solo el texto del error o su charset) o `DATA` (cambia el código, el tipo o los datos).

```bash
# 3. Reporte para revisar y adjuntar: original junto al migrado, caso por caso, con capturas de pantalla
python migrate.py verify report --output <salida> [--browser <msedge|chrome>] [--no-screenshots] \
       [--old-label "JBoss"] [--new-label "Tomcat"]
```

**`report`** no prueba nada: muestra lo que guardaron los dos pasos anteriores. Escribe `reports/evidence/index.html` (resumen
y tabla de casos), una página por caso con la petición enviada y las dos respuestas lado a lado (las líneas que no coinciden
van resaltadas) y `reports/evidence/screenshots/*.png`, una captura por página tomada con Edge o Chrome en modo headless.
Sin navegador las páginas se escriben igual y el resultado avisa que faltan las capturas. Los headers `Authorization` y
`Cookie` de los casos no se copian al reporte.

Salida en `reports/`: `route-structure.{md,json}`, `responses.{md,json}` y `equivalence-evidence.json`, que resume ambas
para el gate G4 (`PASS` exige las dos; `PASS_WITH_ACCEPTED`, `REVIEW` o `FAIL` en otro caso). Código de salida 3 si queda
algo por revisar.

Estas comprobaciones no sustituyen las pruebas con los sistemas reales del cliente (datos, Oracle, servicios externos).

## Pruebas en la plataforma (menú «Pruebas»)

Dentro de cada proyecto, el menú **Pruebas** tiene dos opciones. Requieren el permiso `execute` del proyecto.

### Automáticas: probar una migración (sin preparar nada)

El usuario elige la migración Tomcat WAR (y, si quiere, el contenedor de origen y un paquete de datos) y pulsa **Probar**
(`POST /projects/{id}/http-tests/runs` con `{"workspaceMigrationId": ...}`; trabajo `http_compare` en la cola larga). El worker
(`backend/mf/tomcat_war/autotest.py`; en línea de comandos, `migrate.py verify auto`):

1. Recupera el código analizado (snapshot del análisis) y el WAR de la migración.
2. **Genera la configuración de prueba** de cada lado a partir de las claves que lee el código (incluidos prefijos propios como
   `%[clave]` o `$[clave]`). De los `.properties` del repositorio conserva solo SQL, números, banderas y nombres; **nunca copia
   hosts, URLs, usuarios, contraseñas, JDBC ni brokers**: las URLs apuntan a un simulador local, las bases a una H2 por lado
   (modo Oracle) y los brokers a uno en memoria. El tipo de cada valor faltante sale de cómo lo usa el código (`delay=` es
   número, `cron=` es cron, el setter Java del bean). Cada lado usa sus propios puertos.
3. **Lee los servicios del código**: cada operación REST (de las rutas del WAR, con el alias de su servlet) y cada operación
   SOAP de los WSDL de los endpoints CXF publicados. Un caso por operación.
4. Levanta el original sobre Camel 2 (Jetty) y el WAR en un Tomcat oficial, cada uno en un puerto libre; un módulo que no
   arranca no detiene a los demás. Los datasources que publica otro bundle del servidor se resuelven con la base de prueba
   (`-Dmf.test.jdbcUrl`, solo en pruebas).
5. Compara, genera la evidencia y apaga todo.

Sin paquete de datos es una **prueba de humo**: bases vacías y un simulador que responde 503 a todo sistema externo; se
compara si los dos lados se comportan igual. Con el **paquete de datos de prueba** del cliente
(`POST /projects/{id}/http-tests/data-packages`, un .zip) se usan sus datos:

```text
etc/<ruta>.properties   configuración de prueba; reemplaza a la generada con la misma ruta
sql/*.sql               datos iniciales de las bases (H2, modo Oracle), en orden alfabético
mocks.json              respuestas de los sistemas externos: [{"key": "<clave>", "method": "POST", "contains": "...",
                        "status": 200, "contentType": "text/xml", "body": "..."}]
valores.json            valores de los parámetros de ruta: {"memberNumber": "95715637"}
casos.json              casos extra con el formato de verify responses
```

El resultado dice cuántos módulos arrancaron en cada lado y cuáles no: si un módulo falla en los dos es la configuración o
los datos de prueba; si falla solo en uno, hay que revisarlo.

### Automáticas contra sistemas ya levantados y Manuales

Llaman a los sistemas que indique el usuario, por eso solo aceptan URLs de los servidores en **`MF_TEST_ALLOWED_HOSTS`**
(`host` o `host:port`, por defecto `localhost,127.0.0.1`): el administrador agrega ahí el JBoss de pruebas del cliente y el
Tomcat migrado.

- **Automáticas contra sistemas levantados** (sección avanzada; `"source": "urls"`): la misma batería de casos al
  original (JBoss) y al migrado (Tomcat). Los casos tienen el formato de `verify responses` (REST, XML o SOAP: `method`,
  `headers`, `body`, `contentType`); se cargan desde un archivo, se escriben en pantalla, se reutilizan de la última prueba o
  se traen de «Manuales». El resultado muestra los totales, los casos con diferencias y cada caso con las dos respuestas lado a
  lado. Artefactos: `pruebas-<id>-evidencia.zip` (el reporte HTML de `verify report`, con capturas si el worker tiene Edge o
  Chrome) y `pruebas-<id>-responses.json` (respuestas completas). El trabajo termina `partial_success` si hay diferencias.
- **Manuales** (`POST /projects/{id}/http-tests/manual`, en la API: una sola petición corta): una petición REST, XML o SOAP
  (plantillas, `SOAPAction`, headers libres) a un sistema, o a los dos para compararlos con las mismas reglas. Nada se guarda;
  «Guardar en la batería automática» deja la petición en este navegador para agregarla a una prueba automática.

Los headers `Authorization`, `Cookie` y `Proxy-Authorization` de los casos nunca se devuelven ni llegan al reporte (`***`).

## Verificado (2026-10-08)

- Pruebas: `backend/tests/test_tomcat_war.py` y `test_tomcat_war_verify.py`, 39/39 (fixture sintética `examples/fuse-blueprint`).
- Repositorio piloto real (36 bundles, 636 clases Java, 67 XML Blueprint): salida idéntica archivo por archivo a la prueba
  de concepto validada (1,382 archivos); `--build` PASS; en Tomcat 10.1.60 arrancan 28 de 28 módulos del contenedor
  probado, con 18/18 `healthz` y 12/12 pruebas de humo de punta a punta sobre un módulo.
- `verify routes` sobre el piloto: **PASS**, 756 rutas idénticas (752 Java y las 4 rutas XML activas; otras 2 están
  comentadas en el original) y 10 distintas por parches documentados. Esta comparación
  fue la que detectó 3 rutas que cambiaban de estructura en silencio (hoy cubiertas por parches del cliente).
- `verify baseline-server` + `verify responses` sobre un módulo del piloto (45 casos): **36 idénticos y 9 distintos**
  (`FAIL` hasta que alguien acepte las 9). Todos los casos de éxito coinciden en código, `Content-Type` y cuerpo. De las 9:
  8 son `ERROR_TEXT` (texto de excepciones y de validaciones que imprime cada versión de Camel, páginas 404/405 del
  contenedor) y 1 es `DATA`: un header enviado en minúsculas que el original no reconoce (404) y el migrado sí (200).
- `verify report` sobre ese resultado: 46 páginas y 46 capturas con Edge headless.
- **Prueba automática** (`verify auto`, 2026-10-16) del contenedor `mobile` del piloto, migrado **sin** el overlay manual de
  la prueba de concepto, en modo humo: arrancan **26 de 26 módulos en los dos lados**; 201 casos generados (REST y SOAP de
  todos los módulos). Resultado: **177 idénticos, 23 que solo cambian el texto de un error** (mensajes de excepción de
  CXF/Spring, página 404 de Tomcat contra la de Jetty) **y 1 de datos** (un DELETE sin cuerpo a un servicio que espera JSON:
  pendiente). Antes de las correcciones de esta fecha eran 54 idénticos: esta prueba encontró `SIMPLE_WITHOUT_PLACEHOLDER`,
  el nombre del header de operación SOAP, el *stream caching* y el cuerpo `null` de `Content-Length: 0`. La misma prueba
  corrió por la plataforma (pantalla Pruebas → Automáticas → Probar, worker real) con el mismo resultado.
- Pruebas: `test_tomcat_war_autotest.py` (generador), `test_http_tests.py` (API) y JUnit de la capa (11, `mvn -pl compat test`).

## Claves de configuración

`reports/tomcat-war-report.{md,json}` (`configKeys`) lista las claves que el código lee (`{{clave}}`, `${clave}` de Blueprint,
`@PropertyInject`) y no están en los `.properties`/`.cfg` del repositorio ni en los valores por defecto de Blueprint: deben estar
en la carpeta `etc` del Tomcat destino (`-Dmf.etc`). Es una lista de revisión, no un error.

## Pendiente

- **Plataforma**: corre como migración de workspace (`POST /scans/{id}/workspace-migrations` con `{"target": "tomcat-war",
  "config": {...}, "verifyRoutes": true}`; pantalla Inventario → destino «Tomcat WAR»). El análisis de workspace lo recomienda
  cuando la mayoría de los proyectos son OSGi/Blueprint. Acepta el archivo del cliente salvo `overlay` (carpeta del servidor)
  y, tras compilar, ejecuta `verify routes` (G4) con el Maven del worker. La comparación de respuestas está en «Pruebas»,
  pero el resultado todavía no se registra solo como evidencia del gate G4.
- **Capturas en la plataforma**: la imagen del worker no trae navegador; sin él la evidencia sale sin PNG (las páginas HTML sí).
- **Casos generados**: uno por operación REST/SOAP con valores de ejemplo (`1`) o los de `valores.json`, y un cuerpo vacío
  (`{}` o el elemento de entrada sin contenido). Prueban que los dos lados se comportan igual con esa petición, no los
  escenarios de negocio: para eso están los casos del cliente (`casos.json` del paquete o la batería manual).
- **Sistemas externos** en prueba de humo: responden 503. Lo que el original hace con una respuesta real del sistema
  externo solo se compara con `mocks.json` o contra el ambiente de pruebas del cliente.
- **Rutas REST definidas en XML** (`<rest>`) no generan casos todavía; las de Java sí.
- La línea base nunca se ha comparado contra un JBoss Fuse real.
- Las pruebas del backend no corren en Windows (el worker usa el módulo POSIX `resource`); se ejecutaron en macOS: pasan.

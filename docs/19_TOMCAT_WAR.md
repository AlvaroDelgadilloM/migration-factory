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
| Dónde corre | CLI y plataforma | **solo CLI** (ver «Pendiente») |

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
  compat/                 capa de compatibilidad (código nuevo, 13 clases)
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
| `servlet` | Tomcat entrega los headers HTTP en minúsculas: se restaura el nombre que esperan las rutas |
| `cxf` | `setFaultBody` (se lanza el fault guardado) |

Es código que el equipo debe mantener. No tiene pruebas unitarias propias todavía.

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
python migrate.py verify routes <workspace> --migrated <salida>/migrated-workspace [--config cliente.json] --output <salida>

# 2. Respuestas: misma petición al original y al migrado
python migrate.py verify baseline-server <workspace> --modules a,b,c --servlets Nombre=/alias --etc <etc> \
       [--classpath <jars extra>] [--port 18081] --output <salida>      # primer plano: código ORIGINAL + Camel 2 + Jetty
python migrate.py verify responses --old http://localhost:18081 --new http://localhost:18080 --cases casos.json --output <salida>
```

**`routes`** compila los fuentes originales contra las librerías de Camel 2 del origen, ejecuta `configure()` de cada
`RouteBuilder` en las dos versiones y compara el árbol resultante (el nivel de anidamiento es parte de la comparación).
`rules/tomcat-war/route-equivalences.json` lista lo que no cuenta como diferencia: lo que el motor cambia a propósito y lo
que cada versión solo imprime distinto, cada entrada con su motivo. Una ruta distinta en una clase con parche del cliente
se reporta como «por parche documentado»; cualquier otra es `FAIL`. Límite: no compara rutas definidas en XML.

**`responses`** manda cada caso de `casos.json` a los dos sistemas y compara código HTTP, `Content-Type` y cuerpo (de una
traza de excepción, solo la primera línea). Una diferencia revisada se registra en el caso con `"accept": "motivo"`: sigue
apareciendo en el reporte, como aceptada. La línea base no es Karaf: los beans se cablean con Spring; rutas, procesadores,
XSLT, WSDL y versión de Camel son los originales.

Salida en `reports/`: `route-structure.{md,json}`, `responses.{md,json}` y `equivalence-evidence.json`, que resume ambas
para el gate G4 (`PASS` exige las dos; `PASS_WITH_ACCEPTED`, `REVIEW` o `FAIL` en otro caso). Código de salida 3 si queda
algo por revisar.

Estas comprobaciones no sustituyen las pruebas con los sistemas reales del cliente (datos, Oracle, servicios externos).

## Verificado (2026-10-08)

- Pruebas: `backend/tests/test_tomcat_war.py` y `test_tomcat_war_verify.py`, 35/35 (fixture sintética `examples/fuse-blueprint`).
- Repositorio piloto real (36 bundles, 636 clases Java, 67 XML Blueprint): salida idéntica archivo por archivo a la prueba
  de concepto validada (1,382 archivos); `--build` PASS; en Tomcat 10.1.60 arrancan 28 de 28 módulos del contenedor
  probado, con 18/18 `healthz` y 12/12 pruebas de humo de punta a punta sobre un módulo.
- `verify routes` sobre el piloto: **PASS**, 752 rutas idénticas y 10 distintas por parches documentados. Esta comparación
  fue la que detectó 3 rutas que cambiaban de estructura en silencio (hoy cubiertas por parches del cliente).
- `verify baseline-server` + `verify responses` sobre un módulo del piloto (45 casos): 25 idénticos y 20 distintos
  (`FAIL`). Los casos de éxito coinciden; las diferencias están en errores y encabezados y nadie las ha aceptado todavía.

## Pendiente

- **Plataforma**: no hay perfil en la base de datos, ni job, ni pantallas. Hoy es solo CLI; la evidencia G4 queda en archivos
  y no se registra sola en `POST /executions/{id}/validations`.
- **Casos de la prueba de respuestas**: los escribe quien conoce el servicio. No se generan desde WSDL/Rest DSL.
- **Rutas en XML** fuera de la comparación de estructura.
- **Reporte de configuración**: claves `{{...}}`/`${...}` que el código usa y no están en los `.properties` del origen.
- Pruebas unitarias de la capa de compatibilidad.
- Las pruebas existentes del backend no corren en Windows (el worker usa el módulo POSIX `resource`): la suite completa
  no se ejecutó después de este cambio; hay que correrla en Linux o Docker.

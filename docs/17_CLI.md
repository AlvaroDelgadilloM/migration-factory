# CLI `migrate.py` (camel-migration-tool)

Implementa `camel-migration-tool-docs/` sobre el mismo motor que la plataforma (scanner, catálogo de reglas, perfiles,
recetas OpenRewrite, validación). No usa base de datos, Redis ni Docker. Requiere Python ≥ 3.10 con
`backend/requirements.txt`, Maven 3.9, Git y un JDK 21 (`JAVA_HOME`).

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
export JAVA_HOME=$(/usr/libexec/java_home -v 21)   # macOS; en Linux, la ruta del JDK 21

.venv/bin/python migrate.py analyze  examples/pilot-orders --output ./analysis
.venv/bin/python migrate.py plan     examples/pilot-orders --target springboot
.venv/bin/python migrate.py migrate  examples/pilot-orders --target springboot --java 21 --output ./migration-output \
    --accept CAMEL2_VERSION="Revisado contra la guía 2→3: rutas con endpoints string"
.venv/bin/python migrate.py validate ./migration-output/migrated-project
```

Otros comandos: `migrate.py modernize <proyecto-migrado>` (modernización), `migrate.py plugins` (contrato de plugins).
Opciones de `migrate`: `--auto-apply-up-to AUTO|AUTO_TEST|REVIEW`, `--approve <paso|MOD_*>`, `--modernize`.
Ver `docs/18_MEJORAS_V2_Y_MODERNIZACION.md`.

## Repositorios con varios proyectos (documento 17)

Varios `pom.xml` sin un POM agregador común ya no son `ROOT_AMBIGUOUS`: el origen se clasifica como `SINGLE_PROJECT`,
`MAVEN_MULTI_MODULE` o `MULTI_PROJECT_REPOSITORY` (workspace = ancestro común; nunca se crea un POM padre).

```bash
.venv/bin/python migrate.py analyze  ./develop/commons --output ./workspace-analysis   # automático si es un workspace
.venv/bin/python migrate.py analyze  ./develop/commons --discover-projects             # solo clasificación, grafo, orden y oleadas
.venv/bin/python migrate.py migrate  ./develop/commons --workspace --accept CAMEL2_VERSION="…" --output ./ws-out
.venv/bin/python migrate.py migrate  ./develop/commons --project common-activemq --accept CAMEL2_VERSION="…"
```

- Salida de `analyze`: `projects/<nombre>/analysis.json`, `workspace-report.md`, `dependency-graph.json`,
  `migration-order.json`, `shared-findings.json` (métricas y `COMMON_RULE-NNN`), `waves.json`.
- Grafo interno: dependencias Maven entre proyectos del workspace (`INTERNAL_DEPENDENCY`); orden topológico por oleadas.
  Un ciclo produce `DEPENDENCY_CYCLE` y saca del orden automático solo a esos proyectos y a sus dependientes.
- `migrate --workspace`: oleada por oleada; un proyecto cuya dependencia interna no migró queda `BLOCKED_BY_DEPENDENCY`.
  Los artefactos internos se instalan en dos repositorios locales aislados dentro de la salida (`.m2-legacy` para las
  líneas base, `.m2-migrated` para los candidatos) encadenados al caché compartido con `maven.repo.local.tail`
  (Maven ≥ 3.9): nunca se instala nada en el repositorio local compartido.
- `plan`/`migrate` sin `--project` ni `--workspace` sobre un workspace → `ROOT_SELECTION_REQUIRED` con la lista de proyectos.

## Pipeline (documento 15: POM Repair antes de Recipes)
`source` (solo lectura) → `.work/baseline` → ENVIRONMENT → **MAVEN MODEL CHECK** (`mvn -B -e validate`) → POM REPAIR
(solo si el modelo es inválido) → revalidación → baseline compile → baseline test → análisis → `.work/candidate`
**creado desde el baseline reparado** → recetas → remediación de secretos + re-escaneo → candidate validate →
compile (+ autocorrección acotada) → test → reporte «Migration Run».

- Ninguna receta se ejecuta sobre un modelo inválido: quedan `BLOCKED_BY_BASELINE` (`MAVEN_MODEL_INVALID`).
  Estados de receta: APPLIED, SKIPPED, ROLLED_BACK, BLOCKED_BY_BASELINE, FAILED (TOOLING).
- Errores con `stage` (BASELINE/CANDIDATE), `category` (incluye TOOLING), `blocking`, `autoFixable`, `origin`.
- Secretos: estado inicial PASS/WARN, re-escaneo PASS/BLOCKED, ERROR si falla el escáner.
- `reports/baseline-repair.patch` (reparación) separado de `reports/candidate.patch` (migración).
- La plataforma aplica la misma regla: el worker ejecuta `mvn validate` tras la reparación y, si falla, marca los pasos
  `blocked-by-baseline` y termina con `BASELINE_MODEL_INVALID`.

## Estado real del job (documento 16)
El estado final se calcula, nunca se fija: `SUCCEEDED` (modelo, recetas obligatorias, compile y tests PASS, sin
hallazgos ni tareas manuales), `PARTIAL_SUCCESS` (compila y pasa pruebas, pero hay acciones manuales, hallazgos de
secretos o pasos opcionales no aplicados), `FAILED` (modelo del candidato inválido, receta obligatoria fallida o no
aplicada, compile o tests fallidos) y `BLOCKED` (no se puede continuar con seguridad). Siempre con `primaryReason` y
`reasonCodes` (p. ej. `CANDIDATE_COMPILE_FAILED`, `SKIPPED_DUE_TO_COMPILE_FAILURE`, `MANDATORY_RECIPE_FAILED`).
- `mvn validate` del candidato antes de las recetas y **después de cada receta**: si una receta deja el POM ilegible
  se revierte (`CANDIDATE_MAVEN_MODEL_INVALID`).
- Pruebas omitidas por compilación fallida: `SKIPPED_DUE_TO_COMPILE_FAILURE` (nunca cuentan como éxito).
- Secret scan: `PASS` / `FINDINGS` / `ERROR` (`SCANNER_EXECUTION_FAILED`). Secretos en URIs de endpoints se externalizan
  como `RAW({{secrets.<host>.<param>}})` con `${MF_SECRET_*}`; cada URL externalizada tiene su propia propiedad.
- Repositorios Maven aprobados adicionales: `MF_MAVEN_EXTRA_REPOS="id|https-url"` (p. ej. Shibboleth, necesario para
  `opensaml`, que arrastra CXF WS-Security y no está en Maven Central). El reporte sugiere esta remediación.
- Perfiles y catálogo de reglas son inmutables: cambiar su contenido exige subir la versión (el arranque lo exige y
  retira las versiones anteriores del mismo perfil).

## Pre-flight y baseline (docs 2 / 14-PREFLIGHT-BASELINE-REPAIR.md)
`python migrate.py preflight <proyecto> --output <dir>` (y siempre antes de `migrate`/`analyze`):
1. **Ambiente**: Java/Maven efectivos, arquitectura, tmp escribible/ejecutable, `JAVA_HOME`, acceso al repositorio.
   Maven corre siempre con `-Djansi.tmpdir=<workspace>/.migration-tmp/jansi -Djansi.force=false` (casos `/tmp noexec`).
2. **Modelo Maven**: hallazgos `POM-###` (categoría BUILD_MODEL) por dependencias sin versión, parents, módulos.
3. **Reparación de baseline** (solo en la copia): si las dependencias Camel no tienen versión y no hay BOM, importa
   `org.apache.camel:camel-bom:<versión Camel 2 del POM>` tras verificar que existe en el repositorio. No cambia
   componentes a Camel 4 (`camel-http4` se conserva). Queda en su propio commit y en `candidate.patch`.
4. **Build de referencia** (`mvn -B -e -Dstyle.color=never clean test`, o `clean compile` sin tests) con errores
   clasificados: ENVIRONMENT, BUILD_MODEL, DEPENDENCY, SOURCE, MIGRATION, TEST, SECURITY.
5. **Estado**: BASELINE_SUCCESS / BASELINE_REPAIRED / BASELINE_FAILED_KNOWN / BASELINE_BLOCKED. `migrate` se detiene
   en BLOCKED y en FAILED_KNOWN salvo `--allow-baseline-failure`. Los errores del build del candidato se marcan con
   origen BASELINE (ya existían) o MIGRATION (introducidos). Cada autocorrección lleva `fingerprint` y no se repite.

Salidas: `reports/baseline-report.md` y `analysis.json → baseline {status, buildExitCode, environmentWarnings, repairs}`.
En lugar de un `confidence` numérico, cada reparación registra su **evidencia** (`basis`).

Opciones: `--target springboot|quarkus`, `--java 17|21` (21 añade `UpgradeToJava21`), `--dry-run`, `--no-autofix`,
`--max-fix-rounds 5`, `--fail-on-critical`, `--report-format md,json`, `--skip-baseline`, `--no-maven` (analyze),
`--accept REGLA="motivo"` (decisión humana explícita sobre un hallazgo bloqueante; queda en los reportes).

Códigos de salida: 0 éxito · 1 error de análisis/migración · 2 build fallido · 3 acción manual crítica
(con `--fail-on-critical`) · 4 entrada inválida.

## Estimación de esfuerzo (`effort-estimate.md`)

`migrate`, `analyze --output` y `preflight --output` escriben `reports/effort-estimate.md` y `.json`; la plataforma lo
muestra en **Plan → Estimación de esfuerzo** (`GET /scans/{id}/effort-estimate`).

- Cuenta **unidades de trabajo** (clases CDI, archivos HTTP/REST/JBoss, secretos, integraciones por tipo para pruebas de
  equivalencia, archivos del diff, costos fijos); las reglas de cada categoría están en `rules/effort.json`.
- Las horas por unidad vienen **de su organización**, nunca de la herramienta: `"hours": null` hasta que se defina
  (en `rules/effort.json`, o `MF_EFFORT_CONFIG=<ruta>` para la CLI).
- Calibración: al revisar un hallazgo o un archivo del diff se pueden registrar los minutos dedicados
  (`POST /projects/{id}/effort` o el campo «Minutos dedicados»). La mediana de esas mediciones (horas/unidad) se usa
  para las categorías sin valor configurado; la fuente se muestra por fila (configurado / calibrado (n) / sin calibrar).
- Una categoría sin calibrar no suma horas: el total se reporta como «horas conocidas» y el reporte queda marcado incompleto.

## Salida
```text
migration-output/
├── migrated-project/   copia transformada (sin .git ni target); el origen nunca se modifica
├── reports/            analysis.json, migration-plan.md/.json, migration-report.md/.json, manual-actions.md, candidate.patch
└── logs/               un log por invocación de Maven/OpenRewrite/Git (redactados)
```

## Decisiones respecto a los documentos
- **Sin porcentajes** («readiness 82 %», `confidence` 0–1): por instrucción del proyecto no se inventan. Se reportan
  conteos y la acción de cada paso: AUTOMATIC, AUTOMATIC_WITH_REVIEW, MANUAL, BLOCKED. La precisión de detección se
  indica como `xml-parsed` / `java-regex-approximate`.
- **Copia transformada**, no un proyecto reescrito desde cero: conserva estructura y paquetes; cambia POM (BOMs,
  starters de los componentes usados, incluidos los que JBoss proveía como módulos), Java, Jakarta específico,
  `Application`, `MigratedRoutesConfig` (registra los RouteBuilder como beans), `application.yml`, y elimina
  `jboss-deployment-structure.xml`/`jboss-web.xml`.
- **Ediciones localizadas** (paso `endpoints-config`): `http4/https4 → http/https` con la URL externalizada a
  `services.<host>.url` (`${SERVICES_<HOST>_URL:valor}`), `quartz2 → quartz`, y valores secretos de `.properties`/`.yml`
  reemplazados por `${MF_SECRET_<CLAVE>}`. Cada edición apunta a un archivo, línea y literal exactos; si no coincide,
  no se aplica y queda como acción manual.
- **Prueba de humo generada** (`MigratedRoutesSmokeTest`): instancia los RouteBuilder en un `DefaultCamelContext` sin
  arrancarlo y comprueba los `routeId` detectados. Usa el framework de pruebas que ya tiene el proyecto (JUnit 4 o 5;
  si no hay ninguno añade `spring-boot-starter-test`/`quarkus-junit5`). No sustituye las pruebas de equivalencia.
- **Archivos sensibles** (`.env`, `*.jks`, `*.p12`, `*.pem`, `*.key`, `id_rsa`…) no se copian al snapshot ni a la
  salida; se listan en `manual-actions.md`. G5 revisa el árbol completo del candidato además del diff.
- **CXF**: `camel-cxf` → `camel-cxf-soap`; `cxfrs` → `camel-cxf-rest` (Spring) o paso bloqueado en Quarkus.
- **Inyección**: `javax.inject` → `jakarta.inject` (receta oficial). Spring respeta `@Inject`; la conversión a
  inyección por constructor no se automatiza porque no existe receta pública verificada (solo un visitor interno para
  `@Autowired`), queda como recomendación de revisión. Ámbitos CDI (`@ApplicationScoped`…) se reportan (regla CDI_SCOPE).
- **Reglas en JSON** (`rules/catalog.json`) en lugar de YAML: un solo catálogo para la plataforma y la CLI, sin
  dependencias adicionales; cada regla trae ejemplos positivos/negativos que se prueban.
- **Autocorrección acotada** (máx. `--max-fix-rounds`): solo `package javax.X does not exist` → receta Jakarta
  verificada, y componente Camel ausente → starter/extensión verificada. Cualquier otro error queda como acción manual.
- **Camel 2→3** no tiene receta verificada: sin `--accept CAMEL2_VERSION=...` los pasos automáticos quedan BLOCKED.
- No automatizado (acción manual): conversión a inyección por constructor, conversión de *todas* las properties
  legacy a YAML (solo la configuración generada usa `application.yml`; los `.properties` cargados por el código se
  conservan para no cambiar cómo se leen), definiciones `cxfEndpoint`/WSDL, JTA/XA, EJB, `vm:`.

Además, el paso Camel aplica en la **misma** ejecución de OpenRewrite los artefactos retirados en Camel 4
(`camel-http4→camel-http`, `camel-quartz2→camel-quartz`, `camel-cxf→camel-cxf-soap`, elimina `camel-cdi` si el código
no usa su API), para que el POM nunca quede ilegible; las recetas usan `runNoFork`/`dryRunNoFork` (sin compilar estados
intermedios). APIs del servidor (`javaee-api`, `jboss-jms-api`) → Jakarta EE 10; CDI `@ApplicationScoped` →
`@Component` en Spring; servlet REST → `camel-servlet-starter` + `camel.servlet.mapping.context-path`.

Ejemplos: `examples/pilot-orders` (JMS, JDBC, XML DSL con `vm:`) y `examples/legacy-integrations` (además `http4`,
`quartz2` y un secreto ficticio). Ambos migran con build PASS 4/4 (verificado con Maven/OpenRewrite reales).

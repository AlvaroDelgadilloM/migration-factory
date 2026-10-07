# Improvement pack V2 y modernización

Implementa `camel-migration-tool-improvements/` (00–14) y `12-PROMPT-CLAUDE-MODERNIZATION.md` sobre el mismo motor.
Lo que ya existía (snapshots source/baseline/candidate, POM repair antes de recetas, estados reales del job, rollback por
receta, worker aislado) no se reescribió; esta tabla indica dónde está cada punto.

| Documento | Qué hay | Dónde |
|---|---|---|
| 01 Arquitectura | Ingest → snapshot → baseline → análisis → plan → candidate → build/test → autofix → reporte | `cli_tool.migrate`, `worker/tasks.py` |
| 02 Análisis semántico | `analysis.json`: `runtime`, `java`, `camel`, `risks`, `migrationHints`, `migrationAction` por hallazgo (copy/replace/rewrite/remove/manual-review) y `graph` (rutas, endpoints, enlaces direct/vm/seda/JMS entre rutas, riesgo por nodo) | `analysis/semantic.py` |
| 03 Reglas declarativas | `rules/migration-rules.json` (origen, destino, condiciones, acciones, nivel, rollback); renombres/retiros de artefactos y esquemas salen de ahí | `mf/rules.py` |
| 04 Pipeline / idempotencia | Snapshot (commit) por paso, rollback, mismo input → mismo candidato (prueba `test_same_input_same_candidate`) | `cli_tool.py` |
| 05 Confianza | Nivel por cambio (AUTO, AUTO_TEST, REVIEW, MANUAL) con fundamento; umbral `--auto-apply-up-to` y `--approve <paso>`; estado `PENDING_APPROVAL` | `ledger.py`, `rules.py` |
| 06 Sandbox | `MF_MAVEN_NETWORK=offline\|central\|allowlist`; jansi/`user.home` en el workspace | `worker/mavenops.py` |
| 07 UX | Semáforo verde/amarillo/rojo (con texto) en Hallazgos y Plan | `shared/ui.ts` (`mf-light`) |
| 08 Observabilidad | `GET /api/v1/metrics` (Prometheus, solo admin); log JSON por evento de job; `trace` en `migration-report.json` (motor, catálogo, reglas, snapshot, commit, usuario, fecha, destino) | `api/metrics.py`, `worker/tasks.emit` |
| 09 Pruebas | Fixtures Camel 2 + Spring XML, Camel 3 + Spring Boot, SOAP CXF (+ existentes JBoss, JMS/JNDI, secretos, POM roto) y golden tests | `tests/fixtures`, `tests/golden`, `test_golden.py` |
| 10 Plugins | Contrato id/versión/capacidades/orígenes/destinos/dependencias, validado contra perfiles; `GET /api/v1/plugins`, `migrate.py plugins` | `mf/plugins.py` |
| 11 Supply chain | `sbom.cdx.json` (CycloneDX 1.5, dependencias declaradas efectivas), `checksums.sha256` de lo entregado | `mf/sbom.py` |
| 12 Reportes | `executive-summary.md`, `technical-analysis.md`, `integration-inventory.md`, `security-report.md`, `build-report.md`; puntaje de complejidad con la fórmula del documento (no son horas) | `mf/reports.py`, `effort.complexity_score` |
| 14 Registro de cambios | `changes.json/.md`: por paso regla, archivo, líneas, antes, después, nivel, fundamento, snapshot y parche individual (`reports/steps/NN-paso.patch`, `git apply -R`) | `mf/ledger.py` |

## Decisiones

- **Confianza sin porcentajes.** Los documentos 05 y 12-MODERNIZATION piden un score numérico (0.98…); la regla del proyecto
  «No inventes porcentajes de compatibilidad, confianza o ahorro» prevalece. Se usan los mismos cuatro niveles del plan,
  cada uno con su significado y los factores observados (pruebas, APIs propietarias, endpoints dinámicos, Maven incompleto).
- **AST.** Las transformaciones Java las hace OpenRewrite (AST con tipos). Las ediciones propias son localizadas (archivo,
  línea, literal) o, en inyección por constructor, sobre offsets exactos de un lector estructural que enmascara comentarios
  y literales; todas pasan por build + pruebas y se revierten si fallan.
- **Contenedor por job (06)** no se implementa: exige acceso al socket Docker o a un orquestador, prohibido por la regla
  «Workers aislados, sin privilegios ni acceso al socket Docker del host». El worker sigue aislado, sin root, con límites,
  timeout y cancelación del grupo de procesos.
- **Escaneo de vulnerabilidades (11)**: `NOT_RUN` explícito; no se envía el inventario a servicios externos sin una base aprobada.
- **Camel 3 / Spring XML**: los golden muestran el estado real. `camel-spring` (XML) y `camel-test-spring-junit5` no
  tienen equivalente verificado en el perfil v2 → `UNMAPPED_COMPONENT` bloqueante (fase 5 del roadmap). Los starters
  Camel Spring Boot existentes sí se alinean a la versión del perfil.

## Modernización (`modernize`)

Analiza código **ya migrado** y aplica refactors seguros sobre una copia:

```bash
.venv/bin/python migrate.py modernize ./migration-output/migrated-project --output ./modernization
.venv/bin/python migrate.py modernize ./migrated --output ./mod2 --approve MOD_FIELD_INJECTION
.venv/bin/python migrate.py migrate examples/pilot-orders --accept CAMEL2_VERSION="…" --modernize   # flujo completo
```

Flujo: ANALYZE → BASELINE (`mvn clean test` del proyecto migrado) → por cada refactor: snapshot → aplicar →
`mvn clean test` → si falla o corren menos pruebas que en la línea base, **rollback automático** → REPORT.

| Regla | Tier | Mecanismo | Se aplica |
|---|---|---|---|
| MOD_SAFE_CLEANUP | SAFE | OpenRewrite (`MissingOverrideAnnotation`, `UseDiamondOperator`, `PrimitiveWrapperClassConstructorToValueOf`; rewrite-static-analysis 2.41.0, plugin 6.46.1) | sí |
| MOD_HARDCODED_CONFIG | REFACTOR | URL http(s) literal → `{{services.<clave>.url}}` con el valor actual como default | sí (sin cambio de comportamiento) |
| MOD_SECRETS | REFACTOR | secretos restantes → variables de entorno | sí |
| MOD_FIELD_INJECTION | REFACTOR | `@Autowired`/`@Inject` en campos → constructor (`@Inject` en CDI) | solo con `--approve` (cambia el constructor público) |
| MOD_CONFIG_PROPERTIES, MOD_RESILIENCE, MOD_RECORD_DTO, MOD_EMPTY_CATCH, MOD_LONG_METHOD | REFACTOR | propuesta con código sugerido | no |
| MOD_LOGIC_IN_ROUTE, MOD_LARGE_ROUTE, MOD_ERROR_HANDLING, MOD_OUTBOX | ARCHITECTURE | propuesta | nunca |

Estados: `APPLIED`, `ROLLED_BACK`, `PROPOSED_ONLY`, `BLOCKED` (línea base en rojo o caso no cubierto) y `NO_CHANGES`.
Salidas: `modernized-project/`, `reports/modernization-report.md`, `quality-before.json`, `quality-after.json`,
`refactors-applied.json` (antes/después por archivo y línea, snapshot, parche y rollback), `manual-recommendations.md`,
`reports/refactors/NN-REGLA.patch`, `checksums.sha256`. Umbrales en `rules/modernization-rules.json`.

No se elimina código ni se cambian contratos públicos sin aprobación; el proyecto de entrada nunca se modifica.

## En la plataforma

- **Registro de cambios**: cada ejecución `apply` guarda `changes.json` y un parche por paso (`step-NN-<paso>.patch`).
  `GET /api/v1/executions/{id}/changes`. En la pantalla Ejecución, sección «Registro de cambios» (semáforo por nivel,
  reglas, fundamento, snapshot, antes/después y descarga del parche de rollback).
- **Modernización**: `POST /api/v1/executions/{id}/modernizations` `{"approve": ["MOD_FIELD_INJECTION"]}` crea un job
  `modernize` (permiso `execute`; aprobar reglas que cambian contratos exige `plan`, es decir arquitecto u owner; reglas
  ARCHITECTURE o solo-propuesta → 422). El worker trabaja sobre una copia de `candidate.zip` (que no cambia) y guarda
  `modernization-report.md`, `manual-recommendations.md`, `refactors-applied.json`, `quality-before/after.json`,
  `modernization.patch` y `modernized.zip`. Estado del job: `succeeded`, `partial_success` (propuestas/bloqueos/reversiones
  pendientes) o `blocked` (el candidato no compila o sus pruebas fallan). `GET /executions/{id}/modernizations`,
  `GET /modernizations/{id}`, `GET /modernization-rules`. Tabla `modernization` (migración 0005). En la pantalla
  Ejecución, sección «Modernización» con aprobaciones, log en vivo, resultados por regla, calidad antes/después y descargas.

## Repositorios multi-proyecto (17-CORRECCION-MULTI-PROJECT-ROOT-AMBIGUOUS)

`mf/workspace.py`: clasificación, grafo interno, ciclos (Tarjan), oleadas y hallazgos compartidos. CLI: ver
`docs/17_CLI.md`. Plataforma: un proyecto local/ZIP/Git con varios proyectos se crea y se analiza como workspace
(`summary.workspace`: tipo, proyectos, grafo, orden, oleadas, ciclos, métricas y reglas comunes); Inventario muestra el
panel del workspace con «Analizar proyecto» por proyecto (`POST /projects/{id}/scans {"projectRoot": "<carpeta>"}`).
Planificar un análisis de workspace sin proyecto seleccionado responde `409 ROOT_SELECTION_REQUIRED`.
Ejemplo: `examples/workspace-commons` (common-epay depende de common-datasource).

## Orquestador multiproyecto (18-CORRECCION-MULTIPROJECT-ORCHESTRATOR)

`mf/orchestration/multi_project_orchestrator.py`: `WorkspaceContext` / `ProjectContext` (nunca un "proyecto actual"
mutable). Analiza **todos** los proyectos en paralelo (`MF_WORKSPACE_PARALLELISM`, 4 por defecto); un fallo queda en
ese proyecto (`FAIL`, `PROJECT_ANALYSIS_FAILED`) y el workspace termina `SUCCEEDED`, `PARTIAL_SUCCESS` o `FAILED`.
Estados por proyecto: PENDING, RUNNING, PASS, WARN, FAIL, BLOCKED, SKIPPED. Salidas: `analysis/<proyecto>/analysis.json`
y `report.md`, `workspace.json` (conteos: descubiertos, analizados, PASS/WARN/FAIL), `shared-findings.json` (métricas,
`COMMON_RULE-NNN` y `workspaceRules` `{rule, affectedProjects, totalProjects}`), grafo, orden y oleadas. Logs con
`[analysis][<proyecto>]` / `[migration][<proyecto>]`. La migración sigue el grafo; un dependiente de un proyecto que no
migró queda `{"status": "BLOCKED", "reason": "DEPENDENCY_MIGRATION_FAILED", "dependency": "<proyecto>"}`. La plataforma
usa el mismo orquestador en el análisis de workspace (artefactos `<proyecto>.analysis.json` y `<proyecto>.report.md`).
La migración dentro de una oleada es secuencial: los overlays de repositorio Maven son de proceso.

## Compatibilidad Camel y resultado descargable (19-CORRECCION-CAMEL-COMPATIBILITY-Y-LOCAL-DOWNLOAD)

Parte A:
- `rules/camel/camel-3-to-4-components.json` (registro: SUPPORTED, RENAMED, REPLACED, REMOVED, ARCHITECTURAL_MIGRATION,
  MANUAL_REVIEW) + renombres 1:1 en `rules/migration-rules.json`; `rules/camel/boms/*.json`: listas de artefactos de
  camel-bom 4.14.0/4.14.9/4.22.0/4.22.1, camel-spring-boot-bom 4.14.9 y quarkus-camel-bom 3.40.1 con sha256 del POM.
- `mf/camel_compat.py`: cada dependencia `org.apache.camel` se clasifica; el destino se verifica contra el BOM Camel del
  perfil; el uso real decide (Swagger → `camel-openapi-java` solo con Rest DSL `restConfiguration`/`apiContextPath` y sin
  `SwaggerFeature`; `camel-cxf` → `camel-cxf-rest` si solo hay `cxfrs:`). Lo que no tiene destino válido bloquea el paso
  Camel con un hallazgo `CAMEL_COMPONENT_COMPAT` y código `INVALID_CAMEL_COMPONENT_MAPPING`, `UNSUPPORTED_TARGET_ARTIFACT`,
  `REMOVED_CAMEL_COMPONENT`, `ARTIFACT_REPLACEMENT_REQUIRED` o `ARCHITECTURAL_MIGRATION_REQUIRED` (motivo principal del job).
- Blueprint/OSGi: reglas `OSGI_BUNDLE` y `BLUEPRINT`; `camel-blueprint` es ARCHITECTURAL_MIGRATION, nunca un version bump.
- Starters/extensiones derivados solo si están en el BOM del perfil (snapshot verificado).
- `mf/dependency/artifact_validator.py`: DEPENDENCY_VALIDATION_GATE tras las recetas y antes de compilar (CLI y worker):
  EXISTS / MISSING / REPO_UNAVAILABLE / CREDENTIALS / POLICY / NOT_VERIFIED. Si falla, compile y test quedan `SKIPPED`
  con `DEPENDENCY_VALIDATION_FAILED` (gate G1 en FAIL). Reporte `camel-component-migration-report.md`.

Parte B:
- `mf/output/result.py` (ZIP + manifest) y `mf/integration/adapters.py` (PR_CAPABILITY_GATE): generar el resultado está
  separado de integrarlo. Origen local/ZIP = `LOCAL`; Git = `GIT_REMOTE` (no hay conectores, así que no existe
  `CONNECTED_REPOSITORY`). Nunca se infiere PR por existir `.git`.
- `candidate.zip` se empaqueta al final con el estado real y contiene `.migration/` (manifest.json, migration-report.md,
  manual-actions.md, diff.patch); sin target/, .git ni cachés de IDE. `GET /executions/{id}/result` (acciones según
  origen y estado) y `GET /executions/{id}/download` (`migrated-project.zip`, o `candidate-diagnostico.zip` si falló).
- UI: Ejecución → «Resultado» (Descargar proyecto migrado / con pendientes / candidate para diagnóstico, reporte, diff,
  acciones manuales; «Crear Pull Request» solo para Git y solo con el gate). Validación ya no muestra PR para orígenes locales.
- CLI: `migrated-project.zip` y, con `--workspace`, `migrated-workspace.zip` con el workspace completo (estructura original;
  proyectos no migrados quedan originales y el manifest lo indica por proyecto).

## Migración por oleadas en la plataforma

Desde un análisis de workspace (sin proyecto seleccionado): `POST /api/v1/scans/{scanId}/workspace-migrations`
`{"profileId": "…", "java": "21", "accept": {"CAMEL2_VERSION": "motivo"}, "approve": []}` crea un job
`workspace_migrate` (requiere `execute` y `plan`: aceptar hallazgos bloqueantes para todos los proyectos es una decisión
de arquitecto u owner, registrada en auditoría). El worker restaura el snapshot del análisis y ejecuta el mismo motor que
`migrate.py migrate --workspace`: oleada por oleada, cada proyecto con su línea base y candidato, overlays de
dependencias internas legacy/migradas, `DEPENDENCY_MIGRATION_FAILED` para los dependientes de un proyecto que no migró.
El motor hereda el contexto del job (plazo `MF_WORKSPACE_TIMEOUT_SECONDS`, 4 h por defecto; cancelación del grupo de
procesos; log en vivo). Resultado en la tabla `workspace_migration` (migración 0006): estado por proyecto, oleadas,
conteos y artefactos (`migrated-workspace.zip`, `workspace-report.md`, y por proyecto `migrated-project.zip`,
`migration-report.md`, `manual-actions.md`, `candidate.patch`). `GET /scans/{id}/workspace-migrations`,
`GET /workspace-migrations/{id}`. UI: Inventario → panel del workspace → «Migración por oleadas».
Para revisión de diff por archivo, validación G4 y PR se sigue usando el flujo por proyecto (analizar proyecto → plan → ejecución).

## Análisis y migración por oleadas (20-CORRECCION-ANALISIS-Y-MIGRACION-POR-OLEADAS)

- `orchestration/analysis_wave_orchestrator.py`: oleadas de análisis A1..An del grafo interno (los proyectos en ciclo se
  analizan en una oleada final); paralelo dentro de cada oleada (`MF_WORKSPACE_PARALLELISM`); tras cada oleada se propaga
  `dependencyContext` (reglas detectadas en las dependencias) a los dependientes. Un fallo nunca impide analizar el código
  de un dependiente: queda `ANALYSIS_PARTIAL` (contexto incompleto). Estados por proyecto ANALYSIS_SUCCEEDED / PARTIAL /
  FAILED; por oleada ANALYSIS_WAVE_SUCCEEDED / PARTIAL_SUCCESS / FAILED; global ANALYSIS_SUCCEEDED / PARTIAL_SUCCESS / FAILED.
- `orchestration/migration_wave_orchestrator.py`: por oleada separa RUNNABLE y BLOCKED; los bloqueados quedan
  `{"status": "BLOCKED", "reason": "BLOCKED_BY_DEPENDENCY", "blockedBy": [...]}` (nunca FAILED si no se intentaron); los
  runnable migran (en paralelo con `--parallel N` / `MF_WORKSPACE_MIGRATION_PARALLELISM`; overlays Maven por hilo y
  locks de archivo de Maven). Estados de oleada WAVE_SUCCEEDED / PARTIAL_SUCCESS / FAILED / BLOCKED; global
  MIGRATION_SUCCEEDED / PARTIAL_SUCCESS / FAILED / BLOCKED; workspace SUCCEEDED / PARTIAL_SUCCESS / FAILED / BLOCKED.
- Maven por ProjectContext: siempre `mvn -f <proyecto>/pom.xml`; Maven Execution Guard: sin directorio o `pom.xml`
  legible Maven no se inicia (`MISSING_CANDIDATE_POM`); `MissingProjectException` se clasifica
  `CANDIDATE_BUILD_CONFIGURATION_ERROR` (no error de compilación).
- Salida: `projects/<p>/{analysis, reports, logs, migrated-project}` y `workspace/{analysis-waves.json,
  migration-waves.json, dependency-graph.json, workspace-analysis.md, workspace-migration.md, workspace-state.json, …}`.
  Logs `[workspace][analysis-wave-N|migration-wave-N][proyecto][fase] ESTADO`.
- Reintentos: `migrate --workspace --output <misma salida> --retry <proyecto>` o `--resume`; `workspace-state.json`
  guarda análisis y migración por proyecto. Los exitosos se reutilizan (no se repiten) y sus artefactos vuelven a los
  overlays; los BLOCKED se reevalúan (BLOCKED → READY). Plataforma: `POST /workspace-migrations/{id}/retry`
  `{"projects": [...]}` (vacío = reanudar todos los no exitosos), columnas `retry_of`/`retry` (migración 0007).
- UI: Inventario muestra «Análisis por oleadas» y «Migración por oleadas» por separado (✓ ⚠ ✗ ⊘, «Bloqueado por …»),
  con «Reintentar <proyecto>» y «Reanudar».

## Workspace sin POM agregador (21-CORRECCION-WORKSPACE-SIN-POM-AGREGADOR)

- `mf/build/build_target_resolver.py`: SINGLE_PROJECT → el proyecto; MAVEN_MULTI_MODULE → el POM agregador;
  MULTI_PROJECT_REPOSITORY → `NOT_APPLICABLE` (`WORKSPACE_BUILD_NOT_APPLICABLE`, estado SKIPPED, nunca FAILED). Nunca se
  usa `workspaceRoot/pom.xml` ni se crea un POM agregador.
- Análisis de workspace en la plataforma: sin Maven en la raíz (`[workspace-build] … status=SKIPPED`); POM efectivo por
  proyecto, cada uno en su directorio de trabajo (`MF_WORKSPACE_MAVEN_PARALLELISM`, 2 por defecto); `mavenResolution`
  del scan = `per-project`.
- Maven Execution Guard: `MISSING_CANDIDATE_DIRECTORY` / `MISSING_CANDIDATE_POM` (solo ese proyecto) y, si se intenta
  Maven sobre la raíz de un workspace, `ORCHESTRATION_ERROR` `MAVEN_EXECUTED_ON_NON_PROJECT_ROOT` (nunca error de código).
- Reportes: «Aggregator POM: NOT PRESENT», «Workspace Maven build: NOT APPLICABLE / SKIPPED», «Projects built: N».
  `migrate.py validate <workspace>` valida proyecto por proyecto.

## Colas y decisiones previas

- Trabajos largos (migración de workspace, modernización) en la cola `mf-long` con su propio worker (`worker-long`); los
  análisis y ejecuciones nunca esperan detrás de ellos. Locks de archivo de Maven al compartir `/m2` (`MF_MAVEN_FILE_LOCKS`).
- `409 DECISION_REQUIRED` al lanzar una migración de workspace con proyectos Camel 2 sin `CAMEL2_VERSION=<motivo>`;
  motivo principal `CAMEL_2_TO_3_DECISION_REQUIRED` cuando falta esa decisión en un proyecto.

## Correcciones de compatibilidad Camel (camel-correcciones-compatibilidad 22–31)

Caso `member-phygital`: dependencias Camel con versión literal (la receta oficial solo sube `${camel.version}`),
componentes Swagger, duplicados tras los renombres, `packaging=bundle`.

- **24 · Orden de recetas**: el paso `camel-3-to-4` ejecuta solo la receta oficial; `camel4-compat-mapping` (paso aparte,
  snapshot/diff/rollback propios) aplica el registro como edición localizada del POM (`mf/pom/normalizer.py`): renombres,
  retiros y alineación de versiones literales Camel 2/3 de artefactos soportados a la versión destino. No necesita que
  OpenRewrite resuelva artefactos que aún no existen. Cada receta declara su contrato (`writes`/`reads`).
- **23 · Normalización**: tras cada paso que puede tocar el POM, una sola declaración por (groupId, artifactId, type,
  classifier) y por plugin (se conserva la gestionada por el BOM), `${artifactId}` → `${project.artifactId}` (SAFE_AUTOFIX).
- **25 · Registro**: `camel-swagger` pasa a MANUAL_REVIEW (inspect_usage); un REMOVED nunca queda en el POM destino.
- **27 · OSGi**: `packaging=bundle`, maven-bundle-plugin, Blueprint, `Import/Export-Package` → hallazgo
  `TARGET_RUNTIME_MIGRATION_REQUIRED`, `runtime = osgi-blueprint`, `runtimeMigration` en `analysis.json` y paso manual
  con el plan de migración de runtime (bundle → jar, beans/CamelContext Blueprint, servicios OSGi, MANIFEST).
- **22/26/29 · Gates previos al compile** (`mf/dependency/preflight_gates.py`), en orden: POM_SANITY_GATE,
  CAMEL_COMPONENT_COMPATIBILITY_GATE, CAMEL_VERSION_CONSISTENCY_GATE (mayor ≠ destino o BOM Camel importado de otra
  mayor → `CAMEL_VERSION_CONFLICT`), ARTIFACT_AVAILABILITY_GATE (PASS / NOT_FOUND / REPOSITORY_BLOCKED / AUTH_REQUIRED /
  VERSION_CONFLICT), DEPENDENCY_VALIDATION_GATE. El primer FAIL es la causa raíz; los siguientes quedan SKIPPED y el
  compile `SKIPPED (BLOCKED_BY_PREFLIGHT_GATE)`. En la plataforma son validaciones `pom-sanity`, `camel-compatibility`,
  `camel-version`, `artifact-availability`, `dependency-validation` (gate G1).
- **28 · Errores de compilación** (`mf/compile_errors.py`): errores javac estructurados (archivo, línea, tipo, símbolo,
  confianza como nivel), `UNPARSED_BUILD_FAILURE` cuando no hay errores parseables; autofix por rondas (3 por defecto),
  snapshot por ronda, rollback y parada si la ronda aumenta los errores.
- **30 · Causa raíz**: prioridad ORCHESTRATION_ERROR → … → JAVA_COMPILATION_ERROR → TEST_FAILURE → MANUAL_ACTIONS_PENDING;
  `rootCause` con artefacto, versión, destino, archivo/símbolo y acción recomendada; causas secundarias aparte. El baseline
  distingue `DEPENDENCY_RESOLUTION_ERROR` de `SOURCE`.

## Baseline con dependencias no resolubles (doc 32)
Si el baseline falla por resolución de dependencias, el informe incluye la sección "Resolución de dependencias del baseline"
(artefacto, motivo, categoría, impacto, acción sugerida) y la política decide: continuar, migrar en modo DEGRADADO (stubs
solo para las recetas, build NOT_EXECUTABLE, confianza REDUCED) o bloquear (`MIGRATION_BLOCKED_BY_BASELINE_DEPENDENCY`).
Drivers externos de baja criticidad degradan; artefactos internos, de framework, auth o red bloquean salvo flag explícito.

## CXF por uso, evidencia y plataformas Fuse/Karaf (docs 33–42)
`camel-cxf` ya no se mapea por nombre: su destino sale del uso detectado (SOAP/REST, Spring XML o plano). El reporte Camel separa
el contexto del proyecto de la evidencia propia de cada dependencia (archivo y línea) y da tres niveles de confianza (artefacto,
uso, migración). Si el proyecto usa un BOM de Red Hat Fuse/Karaf que no se puede resolver, el baseline queda
`BASELINE_BLOCKED_PLATFORM_BOM`: el análisis estático y el plan siguen disponibles, las recetas se bloquean y se indica qué
repositorio aprobar. Nunca se sustituye el BOM del proveedor por camel-bom ni se inventan versiones.

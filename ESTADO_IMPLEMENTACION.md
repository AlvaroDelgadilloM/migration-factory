# Estado de implementación — Migration Factory

Fecha: 2026-10-05. Este documento distingue lo **probado** de lo **parcial** y lo **pendiente**. No declara terminado nada sin prueba.

## 1. Funcionalidades terminadas (implementadas y probadas)

| # | Funcionalidad | Evidencia |
|---|---|---|
| 1 | Registro y consulta de proyectos, membresías por proyecto, `If-Match` en cambios | `backend/tests/test_api.py` |
| 2e | Pre-flight (docs 2/14): ambiente, modelo Maven, reparación de baseline con evidencia (camel-bom), build de referencia, estados BASELINE_*, categorías de error, origen BASELINE/MIGRATION, `baseline-report.md`, Jansi tmpdir, fingerprint de autocorrección; artefactos retirados en Camel 4 en el mismo paso; CDI/servlet/APIs de servidor; reglas por categoría (JAVA_LEGACY, TEST_COVERAGE, CAMEL_SERVLET, HARDCODED_URL, DYNAMIC_HTTP, CAMEL_HTTP_HEADERS, MONEY_DOUBLE, JAKARTA_WS/ANNOTATION) | `test_preflight.py` (5); real: `camel2-jboss-legacy-demo` Spring 2/2 y Quarkus 2/2; la misma demo sin BOM → BASELINE_REPAIRED → 2/2; pilotos 4/4 |
| 2d | Transformaciones localizadas (http4/https4→http/https + URL a `services.*.url`, quartz2→quartz, secretos→`${MF_SECRET_*}`), `MigratedRoutesConfig`, `application.yml`, prueba de humo generada, starters de componentes usados, CXF soap/rest, JBoss descriptors eliminados, archivos sensibles excluidos, G5 sobre el árbol completo | CLI y plataforma reales sobre `examples/legacy-integrations`: build PASS 4/4, secreto ausente de toda la salida; `test_cli_tool.py`, `mf.transform` self-check |
| 2c | CLI `migrate.py` (analyze/plan/migrate/validate) sobre el mismo motor; reportes analysis.json, migration-plan.md, migration-report.md, manual-actions.md; logs; códigos 0–4; autocorrección acotada; Java 21 opcional; inventario de integraciones por tipo; reglas QUARTZ2 y HARDCODED_SECRET; borrado de descriptores JBoss | `test_cli_tool.py` (7), ejecución real: piloto Spring/Java 21 build PASS 3/3, origen intacto, `validate` PASS |
| 2b | Explorador de carpetas del servidor (`GET /sources/browse`: solo nombres de carpetas dentro de raíces autorizadas, sin symlinks ni ocultas) y selector de carpeta del navegador (ZIP generado en el navegador → mismo `/uploads` validado) | `test_sources.py::test_server_side_folder_browser`, `zip.spec.ts`, ZIP del navegador validado con `zipfile`/`inspect_zip`, UI verificada |
| 2 | Tres orígenes (`sourceType`): **carpeta local** (directorios autorizados), **ZIP** (subida validada, extracción aislada) y **Git** (https + allowlist + DNS no privado, sin `file://`). Snapshot con hash de contenido para todos; commit además en Git | `test_sources.py`, `test_security.py`, pilotos local y ZIP |
| 3 | Inventario Maven (multi-módulo, parent local, propiedades, dependencyManagement, BOM local) + **POM efectivo con Maven en sandbox**; Java/Camel/XML/rutas/endpoints; valores *observados* vs *resueltos* con fuente | `tests/test_scanner.py`, piloto |
| 4 | Hallazgos con archivo, línea, regla/versión, severidad, clasificación, bloqueo, recomendación y evidencia redactada; deduplicados por regla/archivo/línea | `test_scanner.py`, `test_api.py` |
| 5 | Revisión de hallazgos con motivo obligatorio, máquina de estados, permisos por rol, `If-Match`, historial y auditoría | `test_api.py::test_findings_filters_pagination_and_review` |
| 6 | Perfiles objetivo versionados e inmutables: Spring Boot 3.5.16 + Camel 4.14.9; Quarkus 3.40.1 + Camel Quarkus 3.40.0 (Camel 4.22.1) | `mf/profiles.py`, `test_api.py` |
| 7 | Planes con pasos, precondiciones, dependencias y bloqueos evaluados en vivo; versión/superseded; aprobación | `test_api.py::test_plan_blocks_until_camel2_review` |
| 8 | Preview `rewrite:dryRun` y diff por archivo con atribución de receta | piloto real + `test_worker.py` |
| 9 | Ejecución sobre el snapshot restaurado: un commit interno por paso, rollback automático del paso que falla, descarte de candidato; entrega `candidate.zip` | pilotos + `test_worker.py`, `test_sources.py` |
| 10 | Compilación y pruebas (línea base y candidato) con resultados persistidos (surefire) | piloto real |
| 11 | Progreso, logs redactados (SSE con `Last-Event-ID`), cancelación del grupo de procesos, timeout | `test_worker.py`, UI verificada |
| 12 | Reportes JSON/HTML y descarga autorizada de artefactos con verificación sha256 | `test_api.py`, piloto |
| 13 | Gates G0–G6 y bloqueos de PR; PR borrador **solo para origen Git** (aplica candidate.patch; probado con mock HTTP) | `test_worker.py` |
| — | Seguridad: secretos por referencia `env:MF_CRED_*`, worker que retira secretos de su entorno y es no-dumpable, redacción, path traversal/symlinks, SSRF, contenedores sin privilegios y sin socket Docker, límites | `test_security.py`, `test_worker.py` |
| — | Idempotencia (`Idempotency-Key`, 409 con payload distinto), lease + heartbeat + recuperación de jobs, outbox | `test_worker.py`, `test_api.py` |
| — | Migraciones Alembic (upgrade/check/downgrade) | `test_migrations.py` |
| — | Frontend Angular 21 + PrimeNG 21 (standalone, Signals, rutas lazy): 9 pantallas + login + configuración de proyecto, conectadas a la API | build OK; Cartera, Inventario (scan en vivo) y Hallazgos verificados en navegador |

## 2. Pruebas ejecutadas y resultados (2026-10-05)

| Suite | Comando | Resultado |
|---|---|---|
| Scanner (stdlib) | `python3 -m unittest discover -s tests -v` | **16/16 OK** |
| API + worker + seguridad + migraciones + orígenes | `cd backend && ../.venv/bin/pytest` | **74/74 OK** (SQLite; Maven simulado; local y ZIP sin URL/rama/credencial Git) |
| Frontend (Vitest) | `cd frontend && npx ng test --watch=false` | **10/10 OK** (formulario por origen, escritor ZIP: CRC, deflate/store, filtros) |
| Migración 0002 sobre PostgreSQL con datos previos | `docker compose up` | OK (`alembic_version=0002`; repos previos marcados `git`) |
| Piloto E2E carpeta local + Spring | `python3 scripts/pilot.py` | candidato compile/test PASS 3/3; `candidate.zip` 13 archivos; snapshot igual al re-analizar |
| Piloto E2E ZIP + Quarkus | `python3 scripts/pilot.py --source zip --runtime quarkus` | candidato compile/test PASS 3/3; PR bloqueado por origen no Git |
| Build frontend | `cd frontend && npx ng build` | OK (aviso de presupuesto ajustado a 800 kB) |
| Piloto sintético E2E Spring (Maven + OpenRewrite reales en Docker) | `python3 scripts/pilot.py` | baseline compile/test PASS (3/3); 3 pasos aplicados; candidato compile PASS, test PASS 3/3; G4 NOT_RUN; PR bloqueado (G4 + `vm:` + sin config Git); origen sin cambios |
| Piloto sintético E2E Quarkus | `python3 scripts/pilot.py --runtime quarkus` | 1ª ejecución: **test FAIL** (faltaba `direct`, que en Camel 2 venía en camel-core) → corregido en el planificador → 2ª ejecución: PASS 3/3 |

**Pruebas del producto ≠ pruebas de equivalencia.** Lo anterior prueba Migration Factory sobre una fixture sintética. No demuestra que una aplicación migrada sea equivalente (ver `docs/16_EQUIVALENCIA.md`).

Corrección derivada de una falla intermitente: `kill_group` enviaba SIGKILL a un PGID ya liberado (riesgo de afectar a otro
proceso que lo reutilizara). Ahora espera al líder sin recogerlo (`waitid WNOWAIT` en Linux; verificado en el contenedor).

Defectos encontrados con la demo `camel2-jboss-legacy-demo` y corregidos: el paso Camel dejaba el POM ilegible
(`camel-cdi`/`camel-http4` sin versión en Camel 4); `rewrite:run` compilaba estados intermedios (ahora `runNoFork`);
la reparación duplicaba un `camel-bom` ya importado; `@Inject` sin `jakarta.inject-api` en runtime; la resolución
«maven-effective (tras reparación)» bloqueaba el paso de runtime.

Defectos encontrados y corregidos en esta etapa: (1) reglas nuevas con la misma `catalogVersion` no se cargaban en la
plataforma y un secreto llegó a `candidate.zip` en la primera prueba — ahora el arranque exige subir la versión si el
archivo cambia; (2) el secreto eliminado aparecía en `candidate.patch` y logs de la CLI — ahora se redactan; (3) la
redacción convertía placeholders `${...}` en `***` y G5 daba falso positivo — corregido y probado.

Documento 16 (estado real del job y POM del candidato), verificado con el proyecto del usuario
`camel2-jboss-enterprise-demo` (ZIP «Demo Robusta»):
- Causas encontradas en su ejecución: plan creado con recetas antiguas, perfiles v1 desactualizados en la BD (las
  semillas no actualizaban perfiles), job marcado `succeeded` con fallos, colisión de URLs externalizadas en una misma
  propiedad, falso positivo de secretos en `smtp://host?from=a@b`, `opensaml` no disponible en Maven Central.
- Corregido: validación del modelo del candidato antes y después de cada receta con rollback, agregador de estado
  (SUCCEEDED/PARTIAL_SUCCESS/FAILED/BLOCKED + primaryReason/reasonCodes) común a CLI y plataforma, perfiles v2 con
  protección de inmutabilidad, propiedades únicas por URL, remediación de secretos en URIs, `MF_MAVEN_EXTRA_REPOS`,
  `-Duser.home` en el workspace (error JGit).
- Resultado real: CLI → `SUCCEEDED` en compilación/pruebas con 2 secretos externalizados (rescan PASS) y estado final
  `PARTIAL_SUCCESS` por 40 revisiones manuales; plataforma → 6 recetas aplicadas, compile/test PASS, `partial_success`.
- Pruebas: 78/78 backend, 16/16 scanner; piloto de plataforma 4/4.

## 3. Funcionalidades parciales

- **PR borrador**: (verificado 2026-10-13 contra GitHub real) proyecto Git sobre AlvaroDelgadilloM/migration-factory, raíz examples/pilot-orders, apply con Maven real, revisión independiente → PR borrador #2 (rama mf/35659167, 8 archivos, solo la fixture). Bug encontrado y corregido: `prConfig` se guardaba en snake_case y el worker/UI lo leían en camelCase (KeyError credentialRef); regresión en test_api. GitLab/Bitbucket no implementados.
- **Origen Git**: probado con un repositorio local en pruebas (validación de URL sustituida solo en el test); no se ejecutó un piloto contra un host Git real.
- **Carpeta de mi computadora con plataforma remota**: se sube con el selector de carpeta del navegador (copia, no ruta). Un agente local que lea la carpeta sin subirla no está implementado.
- **Selector de carpeta del navegador**: el diálogo del sistema operativo no se automatizó en la prueba de UI; el ZIP que genera está cubierto por pruebas y validado contra el lector de Python.
- **Nueva versión de ZIP para un proyecto existente**: (resuelto 2026-10-12) `PUT /projects/{id}/upload` (owner) y UI en Proyecto → Origen.
- **OIDC**: validación JWT por JWKS/discovery y flujo Code+PKCE en la UI implementados; **no probado contra un IdP real** (Keycloak no levantado). El modo `dev` sí está probado.
- **Gestor de secretos**: referencias `vault:` aceptadas, pero el adaptador devuelve `VAULT_NOT_CONFIGURED`.
- **ArtifactStore**: solo sistema de archivos local; S3/Blob pendiente.
- **Frontend**: Plan, Ejecución, Diff, Validación, Reglas, Auditoría y Proyecto compilan y están conectadas a la API, pero **no se recorrieron en el navegador** (sesión interrumpida por el límite de uso). Sin pruebas unitarias de frontend.
- **Rutas Java**: detección por regex (aproximada, marcada así en API y UI); sin AST.

## 4. Pendientes

0. Inyección por constructor (sin receta verificada; revisión manual) y conversión de properties legacy a YAML (se conservan para no cambiar su carga).

1. Prueba E2E de UI (Plan → Ejecución → Diff → Validación) y pruebas unitarias Angular (Vitest).
2. Perfil Docker Compose con Keycloak para probar OIDC de extremo a extremo.
3. Re-ejecución manual de suites/fases (botones deshabilitados con explicación en la UI).
4. Carga de archivos de evidencia (deshabilitado con explicación).
5. Aislamiento fuerte de builds: runner efímero por job (Kubernetes Job + gVisor/Kata) y egress restringido por red (hoy: mirror Maven obligatorio en settings, red separada, contenedor sin privilegios).
6. Caché `~/.m2` compartida entre proyectos (riesgo de envenenamiento): aislar por proyecto.
7. Paginación por offset opaco (keyset si los volúmenes crecen).

## 5. Limitaciones verificadas

- `camel-upgrade-recipes` cubre **solo 3.x→4.x**: el paso Camel 2→3 es manual y bloquea hasta que un arquitecto resuelve el hallazgo con motivo.
- `vm:`/`direct-vm:` no se transforman; quedan como paso MANUAL bloqueante del PR.
- El candidato puede compilar y pasar pruebas sin ser equivalente: G4 exige evidencia externa.
- PrimeNG 22 exige clave de licencia (licencia PrimeUI); se eligió PrimeNG 21.1.10 (MIT) con Angular 21.2.25.
- Maven ejecuta plugins del repositorio analizado: se trata como código no confiable dentro del worker sin privilegios.

## 6. Checkpoint: próximos pasos exactos

```bash
cp .env.example .env   # y generar valores aleatorios
docker compose up -d --build
python3 scripts/pilot.py                                  # E2E Spring
open http://127.0.0.1:8080                                # (MF_UI_PORT)
cd backend && ../.venv/bin/pytest && cd .. && python3 -m unittest discover -s tests
```
1. Recorrer en la UI: Plan → crear y aprobar → Generar preview → Ejecutar → Diff (aprobar como `rosa.revisora`) → Validación.
2. Añadir `frontend/src/app/**/*.spec.ts` (Vitest) para `Loader`, `UiError`, `streamEvents` y la pantalla Hallazgos.
3. Añadir `docker-compose.oidc.yml` con Keycloak 26.4.0 (imagen verificada) y probar `MF_AUTH_MODE=oidc`.

## Checkpoint — documento 15 (Corrección del pipeline: POM Repair antes de Recipes)

Hecho en la CLI (`backend/mf/cli_tool.py`, `mf/preflight.py`; 74/74 pruebas con Maven simulado):
- Validador del modelo Maven (`mvn -B -e validate`) antes y después de la reparación; los estados VALID/INVALID quedan en el reporte.
- Árboles `source` (solo lectura) → `.work/baseline` (reparación y validación) → `.work/candidate` (creado desde el baseline reparado).
- Las recetas quedan en `BLOCKED_BY_BASELINE` (motivo `MAVEN_MODEL_INVALID`) si el modelo no es válido; estados APPLIED/SKIPPED/ROLLED_BACK/FAILED.
- Baseline: validate → compile → test por separado. Candidato: validate → compile (+ autocorrección) → test.
- Errores con etapa (BASELINE/CANDIDATE), bloqueo y si son autocorregibles; nueva categoría TOOLING.
- La remediación de secretos es un paso propio (`secret-remediation`) seguido de un re-escaneo PASS/WARN/BLOCKED/ERROR.
- Reporte «Migration Run» con el formato de la sección 16; `baseline-repair.patch` separado de `candidate.patch`.
- Ambiente: espacio en disco y permisos del workspace.

Verificado tras completar el documento 15:
- Pruebas: backend 75/75 (incluye `test_invalid_maven_model_blocks_recipes` del worker), scanner 16/16, build del frontend correcto.
- Migraciones reales con Maven/OpenRewrite: demo sin `camel-bom` → Maven model INVALID → reparación → VALID →
  BASELINE_REPAIRED → todas las recetas APPLIED → candidato validate/compile/test SUCCESS 2/2; demo Quarkus SUCCESS 2/2;
  `pilot-orders` SUCCESS 4/4; `legacy-integrations` SUCCESS 4/4 (secrets detected 1, externalized 1, rescan PASS).
- Plataforma en Docker: el worker ejecuta `mvn validate` tras la reparación (pasos `blocked-by-baseline` y error
  `BASELINE_MODEL_INVALID` si falla), el secret-scan devuelve ERROR ante un fallo de la herramienta y el piloto pasa 4/4
  con el paso `secret-remediation`. La UI muestra los estados nuevos.

## Estimación de esfuerzo (2026-10-06)

- `rules/effort.json` (categorías → reglas, unidad, `hours: null`), `mf/effort.py` (unidades, mediana de calibración, reporte).
- Tabla `effort_log` (migración 0004), `POST/GET /projects/{id}/effort` (permiso `log_effort`; el auditor no registra),
  `GET /scans/{id}/effort-estimate`; minutos opcionales en la revisión de hallazgos y del diff.
- CLI: `effort-estimate.md/.json` en `reports/`. UI: panel en Plan y campo «Minutos dedicados» en Hallazgos y Diff.
- No hay horas por defecto: sin configuración ni mediciones el total es 0 h conocidas y el reporte se marca incompleto.

Verificado: backend 79/79 (incl. `test_effort_estimate_and_calibration`), CLI 8/8, frontend build + 10/10 pruebas;
en Docker la migración 0004 está aplicada y `effort-estimate` para «Demo Robusta» devuelve 19 categorías con unidades
(p. ej. 6 clases CDI, 14 archivos del diff, 4 integraciones JMS), todas «sin calibrar».

## Improvement pack V2 + modernización (2026-10-06)

Detalle y decisiones en `docs/18_MEJORAS_V2_Y_MODERNIZACION.md`.

Implementado:
- Registro de cambios por paso (`changes.json/.md`, parches individuales con `git apply -R`), nivel de confianza
  (AUTO/AUTO_TEST/REVIEW/MANUAL, sin porcentajes), `--auto-apply-up-to` / `--approve`, estado `PENDING_APPROVAL`.
- Reglas declarativas `rules/migration-rules.json` (renombres/retiros Camel 4 y esquemas salen de ahí).
- `analysis.json` semántico: runtime/java/camel, riesgos, `migrationAction` por hallazgo, grafo de integración.
- Reportes: executive-summary, technical-analysis, integration-inventory, security-report, build-report; puntaje de complejidad.
- SBOM CycloneDX + `checksums.sha256`; `trace` (motor, reglas, snapshot, commit, usuario, fecha) en el reporte.
- `MF_MAVEN_NETWORK=offline|central|allowlist`; `GET /api/v1/metrics` (admin); log JSON por evento; `GET /api/v1/plugins`.
- Fixtures Camel 2 + Spring XML, Camel 3 + Spring Boot, SOAP CXF y golden tests; semáforo en Hallazgos y Plan.
- Modernización `migrate.py modernize` / `migrate --modernize` (paquete `backend/mf/modernization`).

Errores encontrados y convertidos en prueba de regresión:
- Hallazgos `UNMAPPED_COMPONENT` añadidos por el plan sin `migrationAction` (golden).
- Starters Camel Spring Boot ya presentes marcados como «sin mapeo»: ahora se alinean a la versión del perfil.
- Inyección por constructor sobre una clase instanciada con `new X()` (MigratedRoutesConfig): ahora BLOCKED con las llamadas.
- `HARDCODED_SECRET` marcaba `RAW({{secrets…}})` ya externalizado: patrón corregido, catálogo **2026.10.4**.
- `mf/rules.py` importaba la configuración (pydantic) y rompía el escáner stdlib (Python 3.9): corregido.

Verificado:
- Backend 92/92, escáner 15/15 (`python3 -m unittest discover -s tests`; la cifra anterior «16/16» no coincide con
  las 15 pruebas actuales del archivo), frontend build + 10/10.
- Corridas reales (Maven + OpenRewrite) `migrate --modernize`: demo Camel 2 → PARTIAL_SUCCESS, modernización con baseline
  PASS 2/2, recetas SAFE ejecutadas (BUILD SUCCESS, sin cambios aplicables); inyección aprobada → ROLLED_BACK por el gate
  (antes del arreglo) y BLOCKED con llamadas (después). Enterprise (Demo Robusta) → PARTIAL_SUCCESS, propuestas
  MOD_RESILIENCE y MOD_ERROR_HANDLING. Docker: métricas y plugins responden con datos reales.

Pendiente / no implementado:
- Contenedor por job (requiere socket Docker u orquestador: contradice la regla de workers aislados).
- Escaneo de vulnerabilidades de dependencias (NOT_RUN explícito); SBOM sin dependencias transitivas.
- (Resuelto 2026-10-06) Modernización y registro de cambios expuestos en la plataforma: ver sección siguiente.
- `camel-spring` (Spring XML) y Camel 3 completo: sin equivalente verificado en el perfil v2 (fase 5).

### Modernización y registro de cambios en la plataforma (2026-10-06)
- API: `GET /executions/{id}/changes`, `POST|GET /executions/{id}/modernizations`, `GET /modernizations/{id}`,
  `GET /modernization-rules`; job `modernize`; tabla `modernization` (migración 0005); permisos: `execute` para
  modernizar, `plan` para aprobar cambios de contrato; ARCHITECTURE/propuestas no aprobables (422).
- UI: secciones «Registro de cambios» y «Modernización» en la pantalla Ejecución.
- Verificado: backend 93/93 (incluye `test_change_ledger_and_modernization_on_the_platform`), frontend build + 10/10.
  En Docker con Maven real: piloto `legacy-integrations` → `changesRecorded: 5` con parches por paso y secreto redactado
  en el antes/después; modernización del candidato de «Demo Robusta» → `partial_success`, baseline PASS, MOD_SAFE_CLEANUP
  sin cambios, MOD_RESILIENCE y MOD_ERROR_HANDLING propuestas, 6 artefactos guardados.
- No verificado: recorrido visual de las dos secciones nuevas en el navegador.

## Repositorios multi-proyecto (17-CORRECCION-MULTI-PROJECT-ROOT-AMBIGUOUS, 2026-10-06)

- `ROOT_AMBIGUOUS` ya no aborta: `mf/workspace.py` clasifica SINGLE_PROJECT / MAVEN_MULTI_MODULE / MULTI_PROJECT_REPOSITORY
  (workspace = ancestro común; finding `ROOT_AMBIGUOUS_RESOLVED_AS_WORKSPACE`; nunca se crea un POM padre).
- Grafo `INTERNAL_DEPENDENCY`, ciclos `DEPENDENCY_CYCLE` (solo esos proyectos y sus dependientes salen del orden automático),
  orden topológico por oleadas, métricas del workspace y `COMMON_RULE-NNN`.
- CLI: `analyze` (automático en workspaces), `--discover-projects`, `--workspace`, `--project`; salidas del documento §14.
  `plan`/`migrate` sin selección → `ROOT_SELECTION_REQUIRED`. `migrate --workspace` por oleadas con overlays de repositorio
  local aislados (`maven.repo.local.tail`, Maven ≥ 3.9): nada se instala en el repositorio compartido.
- Plataforma: proyectos y ZIP multi-proyecto se aceptan; el scan guarda `summary.workspace`; Inventario muestra el panel
  del workspace con «Analizar proyecto» (`projectRoot`); planificar sin proyecto → 409 `ROOT_SELECTION_REQUIRED`.

Errores encontrados en corridas reales y convertidos en prueba:
- La fixture empaquetaba los proyectos comunes como `war` (dependencia `jar` irresoluble): ahora `jar`.
- Con `mirrorOf=*` el resolutor de OpenRewrite espejaba también el repositorio local y no veía el artefacto interno
  migrado (Maven sí). Solo con overlay activo el mirror es `external:*` (todos los repositorios remotos siguen
  obligados a pasar por el mirror aprobado; sin overlay se mantiene `*`).

Verificado:
- Backend 100/100 (`test_workspace.py`: casos de aceptación 1–5, CLI, oleadas, plataforma), escáner 15/15, frontend build + 10/10.
- CLI real `migrate --workspace` (common-datasource → common-epay): ola 1 y ola 2 con baseline PASS, 3 recetas APPLIED,
  candidato compile/test PASS 4/4 cada uno, `PARTIAL_SUCCESS` (acciones manuales); `~/.m2` sin artefactos internos.
- Docker real con `examples/workspace-commons`: scan de workspace (grafo, 2 oleadas, métricas), plan → 409
  `ROOT_SELECTION_REQUIRED`; scan de `common-datasource` con POM efectivo resuelto y plan creado.
- No verificado: recorrido visual del panel de workspace en el navegador; migración por oleadas en la plataforma
  (la plataforma migra un proyecto a la vez; el orden por oleadas se muestra pero no se encadena automáticamente).

## Orquestador multiproyecto (doc 18) y compatibilidad Camel + resultado descargable (doc 19) — 2026-10-06

Detalle en `docs/18_MEJORAS_V2_Y_MODERNIZACION.md` (secciones «Orquestador multiproyecto» y «Compatibilidad Camel»).

Implementado:
- Doc 18: `MultiProjectOrchestrator` (contextos por proyecto, análisis paralelo configurable, fallos aislados, estados por
  proyecto y del workspace, `analysis/<proyecto>/`, `shared-findings.json` con `workspaceRules`), usado por la CLI y por
  el análisis de workspace de la plataforma; migración por oleadas con `DEPENDENCY_MIGRATION_FAILED` + `dependency`.
- Doc 19 A: registro de compatibilidad Camel versionado, BOMs verificados como snapshot, decisiones por uso, hallazgo
  `CAMEL_COMPONENT_COMPAT` con códigos específicos como motivo principal, reglas OSGI_BUNDLE/BLUEPRINT (catálogo
  2026.10.5), renombres netty4/netty4-http/mina2/mongodb3/jetty9 (reglas 2026.10.2), starters/extensiones verificados
  contra el BOM del perfil, DEPENDENCY_VALIDATION_GATE antes de compilar (CLI y worker, gate G1).
- Doc 19 B: origen LOCAL/GIT_REMOTE, PR_CAPABILITY_GATE, `candidate.zip` final con `.migration/`, `/result` y `/download`,
  sección «Resultado» en Ejecución, PR oculto para orígenes locales, `migrated-project.zip` y `migrated-workspace.zip` en la CLI.

Verificado:
- Backend 107/107 (nuevos: `test_camel_compat.py` casos A1–A3, `test_unknown_camel_artifact_blocks_the_camel_step`,
  `test_local_result_is_a_download_not_a_pull_request` con caso Git, orquestador casos 1–5), escáner 15/15, frontend build + 10/10.
- CLI real: pilot + `camel-swagger-java` usado desde Rest DSL → `camel-openapi-java`, gate PASS, compile/test PASS 4/4.
  Workspace real (2 proyectos) con el orquestador: 2 oleadas `PARTIAL_SUCCESS`, `analysis/` por proyecto y
  `migrated-workspace.zip` con ambos proyectos.
- Plataforma real (Docker, piloto local): validación `dependency-validation` PASS, compile/test PASS, `/result` = LOCAL sin PR,
  «Descargar proyecto migrado con pendientes», ZIP `migrated-project.zip` con `.migration/` y sin `target/`.

Errores encontrados y convertidos en prueba: FakeMaven no aplicaba los renombres de `mf.camel.Camel4Artifacts` (el gate
lo detectó); el almacén de artefactos aplana rutas (`analysis/x/report.md` → `report.md`): nombres `<proyecto>.report.md`.

Pendiente / no implementado:
- `CONNECTED_REPOSITORY` (no hay conectores); adaptador de PR solo para GitHub (GitLab/Bitbucket sin adaptador: el gate lo dice).
- (Resuelto) Migración por oleadas en la plataforma: ver sección siguiente. Pendiente: migración paralela dentro de una oleada.
- El registro cubre los componentes conocidos; un artefacto desconocido sin destino en el BOM se bloquea (UNSUPPORTED_TARGET_ARTIFACT).
- Sin recorrido visual en el navegador de las secciones nuevas.

### Migración por oleadas en la plataforma (2026-10-07)
- Job `workspace_migrate` desde un análisis de workspace: mismo motor que `migrate --workspace`, sobre el snapshot del
  análisis, heredando plazo (`MF_WORKSPACE_TIMEOUT_SECONDS`), cancelación y log del job; tabla `workspace_migration`
  (migración 0006); `POST/GET /scans/{id}/workspace-migrations`, `GET /workspace-migrations/{id}`; UI en Inventario.
- Permisos: `execute` + `plan` (aceptar hallazgos bloqueantes para todos los proyectos queda en auditoría).
- Verificado: backend con `test_platform_scan_of_a_workspace_and_project_selection` (5 proyectos, 3 oleadas, permisos,
  una a la vez, artefactos por proyecto y ZIP del workspace) y `test_engine_inherits_the_platform_job_context`.
  Docker real con `examples/workspace-commons`: job `partial_success`; common-datasource (ola 1) y common-epay (ola 2):
  baseline PASS, compile/test SUCCESS 4/4, artefactos por proyecto y `migrated-workspace.zip`.
- Límite: los resultados por oleadas no crean planes/ejecuciones por proyecto en la base de datos; la revisión de diff por
  archivo, la evidencia G4 y el PR siguen el flujo por proyecto.

## Análisis y migración por oleadas (doc 20) — 2026-10-07

- `AnalysisWaveOrchestrator` (oleadas A, contexto heredado, ANALYSIS_PARTIAL) y `MigrationWaveOrchestrator` (RUNNABLE /
  BLOCKED_BY_DEPENDENCY con `blockedBy`, estados de oleada, paralelismo opcional con overlays por hilo y locks de Maven).
- Maven por ProjectContext (`-f <pom>`) y Maven Execution Guard (`MISSING_CANDIDATE_POM`,
  `CANDIDATE_BUILD_CONFIGURATION_ERROR`). Salida `projects/<p>/…` + `workspace/…`; `workspace-state.json`.
- Reintentos: CLI `--retry`/`--resume`; plataforma `POST /workspace-migrations/{id}/retry` (migración 0007).
- UI: análisis y migración por oleadas por separado, «Reintentar <proyecto>» y «Reanudar».
- Verificado: backend (en ese momento) con todas las pruebas en verde, frontend build + 10/10; CLI real (Maven): oleada 1 en paralelo con un proyecto roto →
  dependiente BLOCKED_BY_DEPENDENCY, independiente migrado; `--retry` del roto → migra, desbloquea al dependiente y
  reutiliza el exitoso (4/4 pruebas cada uno); Docker real: análisis A1/A2 y migración M1/M2 WAVE_SUCCEEDED.

Problema reportado por el usuario y corregido: un análisis quedó en cola ~30 min detrás de una migración de workspace
(33 proyectos) lanzada sin la decisión CAMEL2_VERSION (todos los proyectos bloqueados en 2→3; 19 además usan OSGi
Blueprint, que exige migración arquitectónica). Correcciones: cola `mf-long` con worker propio (`worker-long`) para
migraciones de workspace y modernización, locks de archivo de Maven al compartir `/m2`; la API rechaza
la migración de workspace con Camel 2 sin decisión (`409 DECISION_REQUIRED`); motivo principal explícito
`CAMEL_2_TO_3_DECISION_REQUIRED` en lugar de `MANDATORY_RECIPE_NOT_APPLIED`.

## Workspace sin POM agregador (doc 21) — 2026-10-07
- `BuildTargetResolver`; el análisis de workspace ya no ejecuta Maven en la raíz (causa del `MISSING_CANDIDATE_POM` sobre
  `source/develop/pom.xml` visto por el usuario): POM efectivo por proyecto; guard con MISSING_CANDIDATE_DIRECTORY /
  MISSING_CANDIDATE_POM / ORCHESTRATION_ERROR; reportes «Workspace Maven build: NOT APPLICABLE»; `validate` por proyecto.
- Verificado: backend 114/114 (casos 1–6 en `test_workspace.py`), escáner 15/15, frontend build + 10/10. Docker real
  (`workspace-commons`): scan con `[workspace-build] status=SKIPPED`, POM efectivo 2/2 por proyecto, `mavenResolution=per-project`.
- Pendiente de validar con el repositorio real del usuario (33 proyectos): volver a analizarlo.

## Correcciones de compatibilidad Camel (paquete 22–31) — 2026-10-07
- Implementado según `docs/18_MEJORAS_V2_Y_MODERNIZACION.md` (sección «Correcciones de compatibilidad Camel»):
  fases oficial/mapeo, normalización y deduplicación, gates previos al compile, OSGi, clasificador de errores Java con
  rondas y rollback, causa raíz priorizada. Catálogo 2026.10.6, registro de compatibilidad 2026.10.2.
- Verificado: backend 119/119 (`test_compat_corrections.py`), escáner 15/15. CLI real (Maven) con la forma de
  member-phygital (versiones literales 2.24.3, camel-swagger-java desde Rest DSL, camel-http + camel-http4,
  `${artifactId}`): fase 1 + fase 2 aplicadas, duplicado eliminado, 5 gates PASS, compile/test PASS 4/4, sin versiones 2.x.
  Docker real (piloto): validaciones de los 5 gates PASS en el worker, compile/test PASS.
- Error encontrado en la corrida real y corregido: el motivo del baseline decía `SOURCE` ante un artefacto inexistente;
  ahora `DEPENDENCY_RESOLUTION_ERROR`.
- Pendiente: probar con el repositorio real del usuario (member-phygital); los proyectos Blueprint/OSGi siguen requiriendo
  migración de runtime manual (hay plan, no conversión automática).

## Baseline con dependencias no resolubles (32-CORRECCION-BASELINE-DEPENDENCY-RESOLUTION)
- Implementado: `mf/baseline/dependency_resolution_classifier.py` (artefacto, motivo, categoría, criticidad, sugerencia;
  formatos Maven 3.9 `Could not find artifact`, `(absent)`, `dependency: g:a:t:v (scope)`, `was not found in … previous attempt`),
  estados `BASELINE_FAILED_CODE/TEST`, `BASELINE_BLOCKED_DEPENDENCY/REPOSITORY/AUTH/NETWORK/INTERNAL_ARTIFACT`, `BASELINE_UNKNOWN`;
  política CONTINUE / DEGRADED / BLOCKED (`--allow-baseline-dependency-failure`, `--allow-baseline-network-failure`,
  `--allow-baseline-repository-failure` y `MF_BASELINE_ALLOW_*`).
- Modo degradado: stubs (`mf/baseline/stubs.py`) solo en el overlay del run para que OpenRewrite parsee el POM; nunca se
  entregan; la validación que depende del artefacto queda `NOT_EXECUTABLE`; resultado `PARTIAL_SUCCESS` + `MIGRATION_DEGRADED`.
  En workspace, un proyecto degradado sin artefacto instalable bloquea a sus dependientes.
- Confianza: niveles FULL/REDUCED/NONE con factores; el puntaje numérico del §24 no se implementó (regla: sin porcentajes inventados).
- Probado: suites backend 125, scanner 15, frontend 10. CLI real (piloto + `com.oracle:ojdbc6:11.2.0.4` runtime):
  baseline `BASELINE_BLOCKED_DEPENDENCY` (CACHED_RESOLUTION_FAILURE, DATABASE_DRIVER, LOW) → DEGRADED, recetas APPLIED,
  gates PASS, compile PASS, test NOT_EXECUTABLE, `PARTIAL_SUCCESS`, confianza REDUCED. Docker reconstruido.
- Error hallado en la corrida real y corregido: el clasificador no reconocía el formato de log de la fase test de Maven 3.9
  (stubs vacíos → camel-3-to-4 ROLLED_BACK).

## Correcciones CXF + evidencia + Fuse/Karaf (camel-cxf-evidence-corrections 33–42)
- 33 CXF semántico: eliminada la regla global `camel-cxf → camel-cxf-soap` (migration-rules 2026.10.3). `CxfUsageAnalyzer`
  (`camel_compat.cxf_usage`) detecta SOAP/REST/SPRING_XML/BLUEPRINT/JAVA_DSL/JAX_WS/JAX_RS/TRANSPORT; SOAP+Spring XML →
  camel-cxf-spring-soap, SOAP → camel-cxf-soap, REST+Spring XML → camel-cxf-spring-rest, REST → camel-cxf-rest; uso ambiguo →
  `CXF_USAGE_AMBIGUOUS` (revisión manual); SOAP+REST → bloqueo por módulo; siempre `CONDITIONAL`.
  Runtime: no existe starter/extensión `cxf-spring-soap`; se mapea a `camel-cxf-soap-starter` / `camel-quarkus-cxf-soap`
  (verificados en los snapshots). `camel-cxf-spring-rest` queda sin mapeo verificado (hallazgo UNMAPPED). Perfiles → v3.
- 34 Evidencia: el scanner guarda `usageEvidence` (archivo y línea); `ProjectEvidence` (packaging, runtime, Blueprint, Rest DSL,
  CXF) separado de `DependencyEvidence` (type, value, file, line, source) correlacionada por componente; sin evidencia → NONE.
- 35 Confianza: `artifactConfidence`, `usageConfidence`, `migrationConfidence` como niveles con `confidenceFactors`;
  el puntaje numérico sugerido (+30/+25…) no se implementó (regla: sin porcentajes ni puntajes inventados).
- 36–38 Reporte Camel con el formato nuevo (sin "Code usage found"); Blueprint con `Artifact target: N/A` y runtimes destino
  (Spring Boot 3 / Quarkus 3 / Camel Main); SUPPORTED separa artefacto, código (`sourceCompatibility`/`sourceMigration`) y
  runtime/config (`runtimeConfigMigration`) para camel-core, camel-jaxb, camel-servlet.
- 39 Registro v2 (`rules/camel/camel-3-to-4-components.json` 2026.10.3): estado ARCHITECTURAL_MAPPING con `resolver`,
  `targets`, `evidence`, `runtimeOptions`, `actions`.
- 42 Fuse/Karaf: `mf/baseline/vendor_bom_analyzer.py` (PlatformDetector + VendorBomAnalyzer). BOM de proveedor no resoluble →
  `BASELINE_BLOCKED_PLATFORM_BOM`, motivo `PRIVATE_VENDOR_REPOSITORY_REQUIRED` (o `VENDOR_BOM_UNRESOLVED` si el repositorio del
  proveedor ya está aprobado), códigos RED_HAT_FUSE_PLATFORM_DETECTED, BASELINE_REPAIR_PARTIAL, DEPENDENCY_MANAGEMENT_INCOMPLETE,
  VENDOR_BOM_RECOVERY_REQUIRED. Nunca reemplaza el BOM del proveedor ni inventa versiones; camel-bom solo recupera Camel
  (reparación PARTIAL). `dependency-management-snapshot.json`, plan disponible con recetas bloqueadas, análisis PARTIAL_SUCCESS
  (en workspace el proyecto queda PARTIAL y los independientes siguen). Worker y UI (panel "Plataforma").
- No implementado (opcional en el doc 42 §11): inferir versiones desde otros proyectos del workspace.
- Probado: backend 131 (nuevo `tests/test_cxf_evidence.py`: casos 1–6 del doc 40 y Fuse de punta a punta), scanner 15, frontend 10.
  CLI real: fixture SOAP Spring XML → camel-cxf-spring-soap + camel-cxf-soap-starter, gates PASS (el compile del candidato falla
  por opensaml, que no está en Central: problema conocido con remediación documentada). Fuse real con Maven: BOM de Red Hat no
  resoluble en Central → BASELINE_BLOCKED_PLATFORM_BOM, reparación PARTIAL (2 Camel recuperadas, slf4j-api sin versión).
  Docker reconstruido.

## Perfil `tomcat-war`: Fuse/Karaf (Blueprint) → WAR para Tomcat — 2026-10-08

Detalle en `docs/19_TOMCAT_WAR.md`. Cubre el caso que hasta ahora quedaba bloqueado (OSGI_BUNDLE, BLUEPRINT,
TARGET_RUNTIME_MIGRATION_REQUIRED, CAMEL_VM, CAMEL2_VERSION, camel-xmljson) sin rediseñar las rutas.

Implementado:
- `backend/mf/tomcat_war` (stdlib): `blueprint.py` (Blueprint → Spring XML, servicios OSGi, web-fragment de servlets) y
  `pipeline.py` (reglas Java, parches del cliente, POM por módulo verificado contra el BOM, workspace multi-módulo, reportes).
- `rules/tomcat-war/camel2-to-4.json` (26 reglas Java, 30 de dependencias, versiones del perfil) y `template/` (POM padre,
  capa de compatibilidad de 13 clases, WAR). Snapshot `rules/camel/boms/camel-bom-4.18.4.json` (516 artefactos).
- CLI: `migrate.py plan|migrate <workspace> --target tomcat-war [--config] [--build]`; plugins `migration.fuse-blueprint-to-tomcat-war`
  y `target.tomcat-war`. Fixture sintética `examples/fuse-blueprint`.

Verificado:
- `backend/tests/test_tomcat_war.py`: 24/24 (Windows, Python 3.13).
- Repositorio piloto real (36 bundles): salida idéntica archivo por archivo a la prueba de concepto ya validada (1,382
  archivos), `--build` PASS (Maven real), Tomcat 10.1.60 con 28/28 módulos del contenedor probado, 18/18 healthz y 12/12
  pruebas de humo.

No verificado / pendiente:
- (Resuelto) La suite completa del backend se ejecutó en macOS después de integrar el perfil: pasa.
- (Resuelto 2026-10-11/12) Integración en la plataforma, comparación de estructura de rutas automática, reporte de claves de
  configuración y pruebas JUnit de la capa de compatibilidad: ver las secciones de esas fechas.

### Evidencia de equivalencia para el perfil `tomcat-war` (gate G4) — 2026-10-08

- `backend/mf/tomcat_war/verify.py` y `migrate.py verify routes | baseline-server | responses` (docs/19_TOMCAT_WAR.md).
  Herramientas Java en `rules/tomcat-war/verify/` (volcador de rutas por reflexión para Camel 2 y 4, servidor de línea base
  Camel 2 + Jetty, POM de librerías); equivalencias declarativas en `rules/tomcat-war/route-equivalences.json` (22, con motivo).
  `blueprint.convert(camel2=True)` convierte solo el cableado para la línea base. Resumen en `reports/equivalence-evidence.json`.
- Verificado: `test_tomcat_war_verify.py` 11/11 (comparación, grafo de módulos, línea base, prueba diferencial contra dos
  servidores locales, evidencia G4, códigos de salida). Piloto real con JDK 21 y Maven: `verify routes` PASS (752 idénticas,
  10 por parche documentado, 0 sin explicación, 36 módulos comparados); línea base de 3 módulos arrancada con el comando y
  `verify responses` 45 casos → 25 idénticos, 20 distintos (FAIL; diferencias en errores y encabezados, sin aceptar).
- Errores encontrados en la corrida real y corregidos: `javac` no expande `dir/*` dentro de un @argfile (el classpath va en
  CLASSPATH); `urllib` reescribía los nombres de los headers (ahora `http.client`); la línea base quedaba viva ocupando el
  puerto si fallaba su arranque.
- No verificado: la suite existente del backend sigue sin ejecutarse tras estos cambios (no corre en Windows).

## Code review P0/P1 (migration-factory-code-review-fixes 43–50)
- P0-1 (43): `preflight.repair(..., *, exists=None)` resuelve `_artifact_exists` en tiempo de llamada; el monkeypatch funciona
  (la prueba anterior pasaba por red real contra Central).
- P0-2 (44): el plan guarda `decisionContext` (usage, evidencia con línea, endpoints, packaging, plataforma, tests) junto a las
  decisiones Camel; `CAMEL_COMPONENT_COMPATIBILITY_GATE` valida la decisión del plan (`decisionSource=PLAN`) y solo recalcula
  artefactos nuevos con el mismo contexto. Un camel-cxf SOAP/Blueprint ya no vuelve a `CXF_USAGE_AMBIGUOUS`.
- P0-3 (45): con stubs el modelo del candidato es `PASS_WITH_STUBS` (`resolutionMode=DEGRADED_WITH_STUBS`, lista de stubs),
  aviso `RESOLUTION_DEGRADED_WITH_STUBS`; los stubs se retiran del repositorio (marcador `_migration-factory.stub`) antes de
  cualquier validación/build. Migración 0008: `validation.status` String(20).
- P1-1 (46): `mf/dependency/stub_leak_guard.py` antes del build (bloquea compile: `STUB_LEAK_DETECTED`) y antes de empaquetar
  (no se genera el ZIP). CLI y worker.
- P1-2 (47): deduplicación semántica (scope, optional, exclusions, systemPath, version); diferencias →
  `POM_DUPLICATE_SEMANTIC_CONFLICT` (revisión manual, el POM_SANITY_GATE sigue fallando). Única fusión automática con versión
  distinta: Camel sin versión (gestionado por camel-bom) frente a la versión alineada al destino.
- P1-3 (48): `mf/validation_state.py` — un único `validationState` (baselineModel … candidateTest, stubMode, primary/secondary)
  construido igual en CLI, worker y workspace; el estado final sale de él (nunca SUCCEEDED sin resolución real, compile y tests).
  UI: panel "Estado de validación".
- Probado: backend 138 (nuevo `tests/test_code_review_fixes.py` con los 6 casos del doc 49), frontend 10. Maven real (ojdbc6
  runtime): PASS_WITH_STUBS → stubs retirados → STUB_LEAK_GUARD PASS → compile PASS sin stubs → test NOT_EXECUTABLE →
  PARTIAL_SUCCESS. Docker reconstruido con la migración 0008 aplicada en Postgres.

## Tomcat WAR en la plataforma + recomendación + BLOCKED (2026-10-11)
- Diagnóstico del repositorio real «develop 2»: 34 bundles OSGi/Blueprint; con Spring Boot todos quedaban en
  ARCHITECTURAL_MIGRATION_REQUIRED / INVALID_CAMEL_COMPONENT_MAPPING / POM_SANITY_ERROR (por diseño) y se reportaban FAILED.
- Plataforma: migración de workspace con `target: tomcat-war` (sin TargetProfile; migración 0009: `workspace_migration.target`,
  `profile_id` opcional). El worker usa el motor `mf.tomcat_war` sobre el snapshot y compila con `mavenops.mvn` (mirror aprobado,
  repo local aislado, timeout/cancelación del job); guarda ROOT.war, migrated-workspace.zip, reportes y build.log. Sin reintento
  por proyecto (un WAR). No requiere la decisión CAMEL2_VERSION (reglas declarativas con puntos REVIEW).
- Recomendación: `shared.recommendedTarget` en el análisis de workspace (mayoría OSGi/Blueprint); la UI lo muestra y preselecciona
  «Tomcat WAR»; el CLI `--workspace` lo avisa; camel-blueprint lista Tomcat WAR entre sus runtimes destino (registro 2026.10.4).
- Estado: un candidato que nunca compiló porque un gate previo o un hallazgo bloqueante lo detuvo (sin recetas rotas) es BLOCKED,
  no FAILED (regla común `aggregate_status`); una oleada donde todos quedaron así es WAVE_BLOCKED.
- Probado: backend 174 (nuevo `tests/test_tomcat_war_platform.py`), frontend 10. Plataforma real (Docker, Maven real) con «develop 2»:
  recomendación 33/33 → tomcat-war → 33 módulos, 61 Blueprint convertidos, build PASS, ROOT.war, PARTIAL_SUCCESS (2 revisiones manuales).

## Correcciones de la revisión de pendientes (2026-10-12)
- **Bug secret-remediation** (visto en common-epay del repositorio real): (1) `HARDCODED_SECRET` marcaba código que *lee*
  un secreto (`password = exchange.getProperty(...)`, `x = null`, variables) — corregido en el catálogo 2026.10.7 (regla v3,
  15 casos verificados); (2) una edición de secreto no aplicable revertía todas las demás y el paso quedaba ROLLED_BACK
  con "la receta terminó con error" — ahora cada edición es independiente; las no aplicables quedan como acción manual con su
  motivo (`editsNotApplied`, manual-actions.md). Prueba: `tests/test_secret_remediation.py`.
- **Tomcat WAR en la plataforma, completo**: archivo del cliente (`config`, sin `overlay`; migración 0010), comparación de
  estructura de rutas (G4) automática tras compilar con el Maven del worker (`mavenops.external_env`: mirror aprobado, repo
  local, Jansi fuera de /tmp noexec), reporte de claves de configuración (`mf/tomcat_war/config_keys.py`), estado por módulo
  con `routeDifferences`. Real («develop 2»): 502 rutas idénticas y **3 que Camel 4 arma distinto sin error**
  (member-santander, nochespremio-booking-mob/web: nodos que suben de nivel) → PARTIAL_SUCCESS `ROUTE_STRUCTURE_DIFFERENCES`;
  claves: 235 usadas, 232 definidas, 3 deben venir de `etc` (`env`, `pac.url`, `sql.insert.logging`). `verify responses`
  sigue en CLI (necesita sistemas levantados y casos del cliente).
- **Capa de compatibilidad**: 7 pruebas JUnit (direct-vm entre contextos, registro OSGi, xmljson, nombres de headers), corridas
  con Maven real; viajan con el workspace migrado; job `tomcat-compat` en el CI.
- **GitHub Actions**: checkout@v5, setup-python@v6, setup-java@v5, setup-node@v5 (sin avisos de Node 20).
- **Nueva versión de ZIP** para un proyecto existente: `PUT /projects/{id}/upload` (owner, auditado) + UI.
- **Archivos de evidencia**: `POST /executions/{id}/evidence-files` (tipos y tamaño limitados, se sirven como adjunto) enlazados
  a la evidencia registrada (`artifactId`); UI en Validación.
- **OIDC probado contra Keycloak real**: `docker-compose.oidc.yml` + `scripts/oidc_e2e.py` (usuarios con contraseña aleatoria,
  nada en Git): 7/7 (RS256/JWKS/aud/iss, sin token 401, firma alterada 401, otro realm 401, rol mf-admin → admin).
  Flujo Code+PKCE del navegador no automatizado.
- **UI recorrida en el navegador**: panel de migración de workspace con recomendación, destino Tomcat WAR, config, G4 y claves.
- Probado: backend 177, frontend 10, Java compat 7.

Fuera de alcance de este entorno (necesitan servicios o decisiones externas): PR contra GitHub/GitLab reales (token),
Vault, S3, runners efímeros (Kubernetes), escaneo de vulnerabilidades (servicio externo), aislamiento de `~/.m2` por proyecto
(decisión de costo: hoy repositorio compartido + mirror aprobado), botón "Re-ejecutar suites". Ruta Spring Boot: Camel 2→3,
vm/direct-vm y Blueprint siguen siendo decisiones manuales por diseño (no reemplazos ciegos).

## Borrado y retención (2026-10-15)
- `DELETE /executions/{id}` (owner o arquitecto; no en curso): borra la ejecución, sus candidatos, parches, reportes y logs
  (archivos y registros; diff/validaciones/modernizaciones en cascada).
- `DELETE /projects/{id}?confirm=<nombre>` (owner; sin trabajos en curso): borra el proyecto y todo lo generado (análisis, planes,
  ejecuciones, migraciones de workspace, artefactos y ZIP subidos). El origen no se toca.
- Retención `MF_ARTIFACT_RETENTION_DAYS` (compose: 30; 0 = nunca): el worker de la cola mf-jobs borra al arrancar y cada 6 h los
  candidatos (zip/war), parches y snapshots de análisis que no son el último de su proyecto con más de N días. Reportes, logs,
  evidencias y la auditoría se conservan; la ejecución sigue legible.
- La auditoría nunca se borra (sin FK al proyecto): `execution.delete`, `project.delete`, `retention.purge` (actor `system`).
- UI: «Eliminar ejecución» (confirmación) en Ejecución; «Eliminar proyecto» (escribir el nombre) al final de Proyecto.
- Pruebas: `tests/test_purge.py` (3); backend 180, frontend 10.

## JBoss/WildFly WAR → Spring Boot jar o Tomcat 10.1 WAR, probado contra BD real (2026-10-15)
- Ejemplo `examples/jboss-legacy-shop`: WildFly 10.1, Java 8, Camel 2.24.3, Spring XML, datasource JNDI `java:jboss/datasources/ShopDS`,
  PostgreSQL 16 con 3 tablas (5 000 clientes, 300 productos, 50 000 pedidos), 5 servicios REST y 9 casos (`scripts/capture.py`).
- `analysis/jboss_facts.py` (resumen `legacyRuntime`): datasource JNDI y bean, driver JDBC con versión, url-pattern de
  `CamelHttpTransportServlet` en web.xml y context root (jboss-web.xml o finalName). Solo lee el repositorio.
- Runtime Spring desde un WAR de JBoss: se quitan `spring-framework-bom` (Spring 5) y `javax.servlet-api`; se agregan
  `spring-boot-maven-plugin` (repackage), `spring-boot-starter-jdbc` y el driver detectado (runtime); `spring.datasource.*` desde
  `MF_DS_<BEAN>_URL/_USERNAME/_PASSWORD` sin valores por defecto; alias `dataSource`→`<bean>` (MigratedDataSourceConfig) para
  que `#shopDS` siga resolviendo; se conservan `/api/*` y `/shop`.
- `camel.rest.skip-binding-on-error-code=true`: Camel 4 aplica el binding REST también a cuerpos 4xx/5xx y rompía (500) los
  cuerpos de error ya serializados al estilo Camel 2. Encontrado al comparar la evidencia.
- Perfil nuevo `tomcat-10.1-war-spring-boot-3.5-camel-4.14` v1 (proyectos individuales; target CLI `--target tomcat`): mismo motor,
  empaquetado war, `Application extends SpringBootServletInitializer`, `spring-boot-starter-tomcat` provided y se elimina el web.xml
  de JBoss. El perfil `tomcat-war` de workspace (Fuse/Blueprint) no cambia: en un WAR de JBoss no carga su Spring XML ni el JNDI.
- Evidencia (mismo estado de BD antes de cada corrida): `evidence/legacy` (WildFly) = `evidence/migrated` (jar, `java -jar`) =
  `evidence/migrated-tomcat` (WAR en `tomcat:10.1.48-jdk21-temurin`): 9/9 casos con el mismo status y la misma respuesta.
- Pruebas: `tests/test_jboss_runtime.py` (2) y autoverificación de `jboss_facts.py`; backend 182, frontend 12.

## Corrección automática de rutas guiada por `verify` (Tomcat WAR) — 2026-10-16
- `verify routes --fix` (y siempre en la plataforma): por cada ruta con otra estructura, prueba a quitar cada `.end()`/`.endChoice()`
  de uno en uno; recompila la clase, la vuelca y acepta el cambio solo si todas las rutas de la clase quedan idénticas a Camel 2.
  Escribe el fuente corregido, recompila el WAR y lo reporta como «corregida automáticamente (revisar)»; el proyecto queda
  PARTIAL_SUCCESS hasta que alguien confirme que el comportamiento del original era el buscado.
- Sobre el snapshot real (34 módulos): `PointsRoute` (member-santander) y `RequestValidator` (booking-mob y booking-web)
  corregidos; volver a correr `verify routes` da **PASS, 505/505 rutas idénticas**; el WAR compila.
- Límite: solo prueba quitar un `.end()`; agregar uno o hacer dos cambios a la vez no se intenta (queda como diferencia).
- Pruebas: `test_fix_route_keeps_only_the_end_removal_that_restores_the_camel2_tree`; backend 183.

## IBM DataStage → Apache Airflow (migration-factory-datastage-airflow 51–68) — 2026-10-16
- Adaptador nuevo en `backend/mf/datastage/` sobre la misma plataforma: `Project.migration_type` (`JAVA_CAMEL` |
  `DATASTAGE_AIRFLOW`), tabla `datastage_migration` (alembic 0012), job `datastage_migrate` (cola larga), API
  `/projects/{id}/datastage-migrations`, subida de ZIP DSX (`X-Migration-Type`) con las mismas defensas sin exigir Maven,
  UI: tipo de migración al registrar y panel DataStage en Inventario. CLI `migrate.py datastage analyze|migrate`.
- Parser por tokens con IR persistido, registry por evidencia, grafo con oleadas, complejidad/migrabilidad, piloto del doc 67,
  pushdown SQL a Oracle (incluye LAG para «fila anterior» y Merge), DAG por secuencia con TaskGroups, ramas, manejador de
  excepciones y terminadores, Connections/Variables/params, DDL de staging, reconciliación generada y 9 gates.
- Probado con el corpus real BIBES (55 jobs, 10 secuencias; fuera del repositorio): 100 % parseado y clasificado, sin ciclos,
  10 DAGs que importan en DagBag de Airflow 2.10.5 sin errores, sin secretos en la salida, PARTIAL_SUCCESS
  (FUNCTIONALLY_UNVERIFIED hasta reconciliar). El proyecto generado pasa su propia prueba `tests/dags` con Airflow real.
- Hallazgos corregidos al probar con el corpus: aristas productor/consumidor que contradecían el orden de la secuencia,
  cargas de archivo duplicadas (DagBag), contraseñas literales que llegaban al IR (ahora redactadas en el parseo).
- Worker: Airflow 2.10.5 en `/opt/airflow-venv` (constraints oficiales) solo para `DAG_PARSE_GATE`.
- Pendiente: fallback con IA (doc 58, Sprint 4), ejecutar la reconciliación contra DataStage, programación (no viene en el DSX).
- Pruebas: `tests/test_datastage.py` (10; 2 se activan con `MF_AIRFLOW_PYTHON` y `MF_DATASTAGE_CORPUS`); frontend 12.
- Detalle: `docs/20_DATASTAGE_AIRFLOW.md`.

## Interfaz: detección de tipo, menú por tipo, estados legibles y lista de trabajo (2026-10-19)
- Registrar proyecto es un asistente de 3 pasos (Origen → Tipo → Datos). El ZIP se sube con `X-Migration-Type: AUTO` y la
  plataforma detecta el tipo por su contenido (`.dsx` = DataStage, `pom.xml` = Java/Camel); si no trae ninguno, el error dice
  qué contiene. Git y carpeta local eligen el tipo a mano.
- Menú lateral según el tipo de proyecto: Java/Camel conserva Inventario…Validación y Pruebas; DataStage muestra Resumen,
  Jobs, Oleadas y Acciones manuales (rutas `/p/:id/ds…`; el antiguo panel dentro de Inventario se eliminó).
- Resumen DataStage: estado en palabras, «siguiente paso», avance, controles de calidad con nombre legible, piloto y una
  descarga principal del proyecto Airflow. Jobs: tabla a todo el ancho con filtros y detalle lateral (pasos generados, SQL,
  dependencias). Oleadas: columnas por oleada con el estado de cada job. Acciones: lista de trabajo agrupada por categoría,
  con casillas persistidas (`PATCH /datastage-migrations/{id}/actions`, auditado; alembic 0013 `action_state`) que se
  conservan al volver a migrar.
- Estados con etiqueta en español y el código técnico en el tooltip (PARTIAL_SUCCESS → «Migrado con pendientes», etc.).
- Cartera: columna y filtro por tipo de migración (`GET /projects?migrationType=`), destino legible, ancho máximo 1840 px.
- Pruebas: backend 203, frontend 17. Revisión visual en navegador pendiente: la extensión de Chrome se desconectó por el
  cambio de cuenta.

## Rediseño visual (2026-10-19)
- Sistema visual nuevo en `styles.css`: tokens de color, radios, sombras suaves, tarjetas con ícono, tablas y diálogos
  PrimeNG pulidos, botones y etiquetas sin cortes de línea.
- Login en dos paneles: propuesta de valor (Java/Camel, DataStage → Airflow, validado y trazable) y acceso; en modo
  demostración cada perfil es una tarjeta con avatar y lo que puede hacer. OIDC con un solo botón.
- Menú lateral con logo, tarjeta del proyecto activo (nombre + tipo), secciones Proyecto/Pruebas/Plataforma; barra superior
  con ruta (Cartera › proyecto), su rol, avatar del usuario y salida.
- Cartera: saludo, tarjetas por tipo, búsqueda con ícono, tabla sin columnas técnicas (commit/snapshot) y con una columna
  «Estado» que también refleja la última migración DataStage (`lastDataStage` en el proyecto).
- DataStage: subtítulos que explican cada página, tarjetas con ícono, botón principal de descarga, leyenda de oleadas con
  color, grupos de acciones con contador.
- Revisado con capturas en 1440 px (Playwright local, no forma parte del repositorio). Frontend 17, backend sin cambios de
  comportamiento salvo `lastDataStage`.

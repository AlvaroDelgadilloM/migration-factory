# Implementación (2026-10-05)

## Componentes
- `backend/mf/analysis`: scanner estático (stdlib). XML con expat (líneas, sin DTD/entidades), comentarios Java/XML ignorados,
  rutas XML parseadas y Java por regex (marcadas `java-regex-approximate`), endpoints literales vs dinámicos, URIs redactadas,
  modelo Maven multi-módulo con parent local, propiedades, dependencyManagement y BOM local. `apply_effective` incorpora
  `mvn help:effective-pom` (ejecutado por el worker) con procedencia `maven-effective`.
- `backend/mf/api`: FastAPI `/api/v1` (OpenAPI en `/api/v1/openapi.json`, docs en `/api/v1/docs`). Errores
  `{code,message,requestId,details}`; 404 si el proyecto no es visible, 403 si el rol no basta; `If-Match` en proyecto,
  hallazgo, plan y diff; `Idempotency-Key` en scans, ejecuciones y PR.
- `backend/mf/worker`: proceso separado (RQ `SimpleWorker` + dispatcher). Claim por lease en BD, heartbeat, recuperación de
  leases expirados (reencola o falla), outbox transaccional. Cada intento usa un workspace nuevo que se borra al terminar.
- `backend/mf/planning.py` y `gates.py`: plan y gates. `profiles.py`: perfiles sembrados con versiones verificadas.
- `frontend/`: Angular 21 standalone, Signals, rutas lazy, PrimeNG 21; tipos generados desde OpenAPI
  (`npx openapi-typescript openapi.json -o src/app/core/api-types.ts`).

## Orígenes y snapshots
`backend/mf/worker/source.py`: `git` (clone por commit), `local` (copia sin seguir enlaces; los que escapan abortan) y `zip`
(`security.inspect_zip` en la API al subir, `safe_extract_zip` en el worker: rutas, symlinks, cifrado, ratio, tamaño real).
Raíz Maven = pom.xml menos profundo (falla si es ambiguo). `make_snapshot` genera un tar.gz determinista y un hash de
contenido; las ejecuciones restauran el snapshot (filtro `data`, verificación de hash) y crean un repositorio Git
**interno** determinista solo para commits por paso, diff y rollback. Entregables: `candidate.zip`, `candidate.patch`,
reportes. El PR (solo `git`) aplica `candidate.patch` sobre un clone del commit base y empuja una rama `mf/*`.

## Pasos del plan
`baseline` (G0) → `camel-2-to-3` (manual si hay Camel 2) → `camel-3-to-4` (`org.apache.camel.upgrade.CamelMigrationRecipe`)
→ `jakarta-<api>` (solo APIs detectadas) → `runtime-<spring|quarkus>` (receta YAML compuesta con primitivas verificadas:
AddManagedDependency de BOMs, ChangeDependencyGroupIdAndArtifactId a starters/extensiones, AddDependency de componentes
core usados y XML DSL, ChangePackaging war→jar, CreateTextFile de `Application.java`/`application.properties`) →
pasos manuales (vm, EJB, JTA, seguridad, EAR, componentes sin equivalente) → `behavior-review` → compile/test/secret-scan
→ `equivalence` (G4) → `diff-review` (G6). Una dependencia Camel sin equivalente verificado crea un hallazgo
`UNMAPPED_COMPONENT` y bloquea el paso de runtime.

## Versiones verificadas en Maven Central
| Perfil | BOMs | Recetas |
|---|---|---|
| spring-boot-3.5-camel-4.14 v1 | camel-spring-boot-bom 4.14.9, spring-boot-dependencies 3.5.16 | plugin 6.15.0, camel-upgrade-recipes 4.14.0, rewrite-migrate-java 3.11.0 |
| quarkus-3.40-camel-quarkus v1 | quarkus-bom 3.40.1, quarkus-camel-bom 3.40.1 | plugin 6.46.1, camel-upgrade-recipes 4.22.0, rewrite-migrate-java 3.42.0 |
Los nombres de recetas se leyeron de `META-INF/rewrite/*.yml` dentro de esos jars. Nada usa LATEST/RELEASE.

## Seguridad
- Secretos: `.env` (no versionado); credenciales Git por referencia `env:MF_CRED_*`; el worker las captura, las borra de
  `os.environ` y se marca no-dumpable; los subprocesos reciben un entorno mínimo sin credenciales de BD/Redis.
- SSRF: https, sin credenciales en URL, host en allowlist, todas las IP resueltas públicas (salvo hosts internos declarados),
  revalidación antes del clone; git con `protocol.allow=never` salvo https, sin redirecciones, hooks desactivados.
- Ejecución: `shell=False`, grupo de procesos propio, SIGTERM→SIGKILL al cancelar o por timeout, rlimits en Linux,
  `settings.xml` con mirror obligatorio, contenedor sin privilegios (`cap_drop: ALL`, `read_only`, `no-new-privileges`,
  límites de CPU/RAM/PIDs), sin socket Docker. Enlaces simbólicos que salen del checkout abortan el job.
- Redacción de tokens, contraseñas, JWT, claves y userinfo en logs, diffs y reportes; descargas como adjunto con sha256.
- Limitación: Maven ejecuta plugins del repositorio dentro del contenedor worker, que tiene red hacia BD/Redis. En producción
  usar un runner efímero por job sin credenciales (pendiente).

## Decisiones
- RQ (no Celery): una sola tecnología de cola, simple; la entrega fiable la da el lease en BD + outbox.
- PrimeNG 21.1.10 (MIT) en vez de 22 (licencia PrimeUI con clave) → Angular 21.2.25.
- Contexto de proyecto en la URL `/p/:id/...`; tokens dev en `sessionStorage`; SSE por `fetch` con cabecera Authorization.
- La CSP impide el CSS crítico inline de Angular (`inlineCritical: false`).

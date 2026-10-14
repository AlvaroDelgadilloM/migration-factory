# Migration Factory — JBoss + Camel 2 → Camel 4 (Spring Boot / Quarkus)

Plataforma para inventariar, planificar y ejecutar de forma controlada migraciones de aplicaciones JBoss + Apache Camel 2
hacia Camel 4 con Spring Boot o Quarkus. Estado real y pruebas: **[ESTADO_IMPLEMENTACION.md](ESTADO_IMPLEMENTACION.md)**.

| Capa | Implementación | Versión fijada |
|---|---|---|
| UI | Angular standalone + Signals + PrimeNG (`frontend/`) | Angular 21.2.25, PrimeNG 21.1.10 (MIT) |
| API | FastAPI + Pydantic (`backend/mf/api`) | ver `backend/requirements.lock` |
| Datos | PostgreSQL + SQLAlchemy + Alembic | postgres:17.6 |
| Jobs | Worker Python separado + Redis + RQ (`backend/mf/worker`) | redis:8.2.2, rq 2.12.0 |
| Transformación | OpenRewrite vía Maven en el worker | rewrite-maven-plugin 6.15.0 / 6.46.1 |
| Identidad | OIDC (JWKS) o modo `dev` con usuarios ficticios | — |

## Arranque local (Docker)

Requisitos: Docker con Compose v2. Puertos: UI `127.0.0.1:${MF_UI_PORT:-3240}`, API `127.0.0.1:${MF_API_PORT:-3241}`.

```bash
cp .env.example .env
# Sustituir los valores con aleatorios, por ejemplo:
sed -i.bak "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 16)/; s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=$(openssl rand -hex 16)/; s/^MF_DEV_JWT_SECRET=.*/MF_DEV_JWT_SECRET=$(openssl rand -hex 32)/" .env
docker compose up -d --build
docker compose ps            # api healthy, worker y frontend running
```

Abrir `http://127.0.0.1:3240` y entrar con un usuario de demostración (modo `dev`, identidades ficticias):
`ana.arquitecta` (owner), `diego.dev` (developer), `rosa.revisora` (reviewer), `aldo.auditor` (auditor), `admin`.
Al arrancar se crea el proyecto **«Demo Orders (fixture sintética)»** como carpeta local `/sources/pilot-orders`.

### Orígenes de proyecto (`sourceType`)
| Origen | Campos | Notas |
|---|---|---|
| `local` — Carpeta local | ruta absoluta (botón **Examinar…**: explorador del servidor limitado a los directorios autorizados) | Ruta **del equipo donde corre el worker**. En Docker, `MF_HOST_SOURCES_DIR` (por defecto `./examples`) se monta en solo lectura como `/sources`; solo se aceptan rutas bajo `MF_LOCAL_SOURCE_ROOTS`. Si la plataforma es remota se deshabilita: use ZIP (una ruta de su computadora requiere un agente local). |
| `zip` — Archivo ZIP | archivo ZIP **o una carpeta de su equipo** (el navegador la comprime sin `.git`/`target`/`node_modules`; no envía su ruta) | Se valida tamaño, entradas, ratio de compresión, rutas, enlaces simbólicos y raíz Maven antes de guardarlo; se extrae aislado en el worker. |
| `git` — Repositorio Git | URL https, rama, referencia de credencial | Hosts permitidos y SSRF como antes; nunca `file://`. Único origen con ramas y PR borrador. |

Todo análisis crea un **snapshot** (tar.gz + hash de contenido; en Git también el commit). Preview y ejecución trabajan
sobre ese snapshot, nunca sobre el origen. El candidato se entrega como `candidate.zip`, con `candidate.patch` y reportes.

Flujo: Cartera → Inventario (Analizar) → Hallazgos (resolver `CAMEL2_VERSION` con motivo) → Plan (crear, aprobar, preview,
ejecutar) → Ejecución → Diff (aprobar como revisora) → Validación (gates y PR).

## Despliegue en la VPS con Portainer (GitHub Actions)

Actions → **Deploy VPS (Portainer)** → Run workflow: corre el CI, publica las imágenes en GHCR
(`ghcr.io/<owner>/mf-{api,worker,frontend}:<commit>`), abre un túnel SSH a la VPS y crea o actualiza el stack
`migration-factory` por la API de Portainer en `127.0.0.1` (Portainer no se expone a internet). La VPS no compila y no existe
ningún `.env`: las variables del stack salen de los secrets del environment `vps cpontabo`.

| Secret (environment `vps cpontabo`) | Valor |
|---|---|
| `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_SSH_PASSPHRASE`, `VPS_KNOWN_HOSTS` | acceso SSH (ya configurados) |
| `PORTAINER_API_KEY` | Portainer → My account → Access tokens |
| `PORTAINER_ENDPOINT_ID` | id del entorno (número en la URL de Portainer al abrirlo) |
| `POSTGRES_PASSWORD`, `REDIS_PASSWORD` | `openssl rand -hex 16` |
| `MF_DEV_JWT_SECRET` | `openssl rand -hex 32` |
| `MF_CRED_GITHUB` | opcional (PR borrador) |

Variables opcionales del environment: `PORTAINER_PORT` (9443), `MF_ENV`, `MF_AUTH_MODE`, `MF_OIDC_ISSUER`, `MF_OIDC_AUDIENCE`,
`MF_OIDC_JWKS_URL`, `MF_HOST_SOURCES_DIR` (por defecto `/opt/mf-sources`, créela en la VPS). Las imágenes de GHCR deben ser
públicas o Portainer debe tener el registro `ghcr.io` con un token `read:packages`. Delante: nginx del host
(`docker/vps-nginx-http.conf`).

## OIDC de punta a punta en local (Keycloak)

```bash
# .env: KEYCLOAK_ADMIN_PASSWORD=<aleatorio>
docker compose -f docker-compose.yml -f docker-compose.oidc.yml up -d   # API y workers en MF_AUTH_MODE=oidc, Keycloak en 127.0.0.1:3242
.venv/bin/python scripts/oidc_e2e.py                                    # usuarios de prueba con contraseña aleatoria + comprobaciones
docker compose up -d                                                    # volver al modo dev
```

Realm `mf` (`docker/keycloak/realm-mf.json`): cliente público `migration-factory-ui` (Code + PKCE, audiencia `migration-factory`),
rol de realm `mf-admin` = administrador de la plataforma. Keycloak corre en modo dev: solo para pruebas locales.

## Validar

```bash
# Piloto sintético de extremo a extremo contra el stack en marcha (Maven y OpenRewrite reales; 5–10 min la 1ª vez)
python3 scripts/pilot.py                                  # carpeta local, Spring Boot
python3 scripts/pilot.py --source zip --runtime quarkus   # ZIP, Quarkus
python3 scripts/pilot.py --source git --repo https://github.com/<org>/<repo>.git   # requiere red y host permitido

# Pruebas del producto
python3 -m unittest discover -s tests -v        # scanner (stdlib, Python ≥ 3.9)
python3.12 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt
cd backend && ../.venv/bin/pytest && cd ..      # API, worker, seguridad, migraciones
cd frontend && npm ci && npx ng build && cd ..
```

El scanner sigue usable sin la plataforma: `python3 src/scanner.py examples/legacy --output /tmp/factory-demo`.

## Reglas de migración aplicadas

No se reemplaza `javax.*` en bloque (solo recetas Jakarta específicas por API detectada); `vm:`/`direct-vm:` nunca se cambian por
`seda:`; no se cambia broker, datasource ni `jdbc`→`sql`; XML DSL se conserva; JMS, JTA/XA, EJB, seguridad y clustering
generan pasos de revisión o manuales. Camel 2→3 no tiene receta verificada: es un paso manual bloqueante. El origen
(carpeta, ZIP o repositorio) nunca se modifica y no hay despliegue.

## CLI sin plataforma (`migrate.py`)

```bash
export JAVA_HOME=<JDK 21>
.venv/bin/python migrate.py migrate examples/pilot-orders --target springboot --java 21 --output ./migration-output \
    --accept CAMEL2_VERSION="Revisado contra la guía 2→3"
```
`preflight`, `analyze`, `plan`, `migrate`, `validate`; mismo motor que la plataforma. Detalle: `docs/17_CLI.md`.

## Estimación de esfuerzo

Unidades de trabajo por categoría × horas que define su organización en `rules/effort.json` (vacías por defecto)
o calibradas con los minutos registrados al revisar hallazgos y archivos del diff. Ver `docs/17_CLI.md`.

## Mejoras V2 y modernización

Registro de cambios por paso (regla, líneas, antes/después, nivel de confianza y parche de rollback), reglas
declarativas (`rules/migration-rules.json`), umbral de auto-aplicación, SBOM y checksums, reportes ejecutivos,
métricas Prometheus, contrato de plugins y golden tests. `migrate.py modernize` (o `migrate --modernize`) aplica
refactors SAFE/REFACTOR con build + pruebas por lote y rollback automático; ARCHITECTURE solo se propone.
Ver `docs/18_MEJORAS_V2_Y_MODERNIZACION.md`.

## Fuse/Karaf → Tomcat WAR (perfil `tomcat-war`)

Para bundles OSGi + Blueprint que deben pasar a Camel 4 sin rediseño: un WAR con Spring XML y una capa de compatibilidad
(`direct-vm`, `xmljson`, servicios OSGi). CLI: `python migrate.py migrate <workspace> --target tomcat-war --output <salida>`; plataforma: Inventario → migración de workspace → destino «Tomcat WAR» (se recomienda sola en workspaces Fuse/Blueprint).
Ver `docs/19_TOMCAT_WAR.md`.

## Documentación

`docs/01`–`docs/14` (especificación original actualizada), `docs/15_IMPLEMENTACION.md` (arquitectura implementada,
seguridad y decisiones), `docs/16_EQUIVALENCIA.md` (cómo validar una aplicación migrada), `docs/17_CLI.md` (CLI).

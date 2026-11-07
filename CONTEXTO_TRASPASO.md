# Contexto de traspaso (para otra IA) — 2026-11-06

Léeme primero. Detalle verificado en [ESTADO_IMPLEMENTACION.md](ESTADO_IMPLEMENTACION.md); docs por tema en `docs/01..21`.

## Qué es
Migration Factory («Cóndor»): plataforma que analiza, planea, migra y valida proyectos legados. Tipos de migración:
- **Camel 2 → 4** (Spring Boot 3.5 / 4.1 con Java 25, Quarkus 3.40, Tomcat 10.1 WAR; orígenes JBoss/WildFly, Fuse/Karaf Blueprint) — el núcleo, maduro.
- **DataStage → Airflow** (`backend/mf/datastage`) — hecho, con IA de respaldo y programación.
- **Odoo 12 → 19** (`backend/mf/odoo`) — base + código a la medida + pruebas de flujos + Odoo 19 de prueba; e2e verde.
- **Oracle APEX** (`backend/mf/apex`) — solo fase 1 (análisis). Fases 2–3 pendientes (ver `docs/21_APEX.md`).

## Dónde está cada cosa
- Backend FastAPI + SQLAlchemy + Alembic: `backend/mf` (API `mf/api`, worker RQ `mf/worker`, motor `mf/analysis`, `mf/transform.py`, `mf/planning.py`).
- Frontend Angular 21 + PrimeNG 21 (MIT; no subir a 22, exige licencia): `frontend/`.
- CLI sobre el mismo motor: `migrate.py` (analyze/plan/migrate/validate/modernize/datastage/verify/agent).
- Reglas: `rules/catalog.json` (**subir `catalogVersion` en cada cambio**, si no la semilla se niega). Perfiles inmutables: todo cambio de mapeo = nueva versión.
- Ejemplos sintéticos: `examples/` (jboss-pedidos-completo es la demo grande). Corpus reales de clientes (BIBES DataStage, exports APEX) **nunca** al repo.
- CI: `.github/workflows/` (ci, ui-e2e, odoo-e2e, datastage-e2e, jboss-pedidos-e2e, deploy*).

## Cómo probar
```bash
cd backend && ../.venv/bin/pytest          # backend
python3 -m unittest discover -s tests      # scanner
cd frontend && npx ng test --watch=false && npx ng build
python3 scripts/pilot.py                   # E2E real (Docker + Maven)
```

## Despliegue
- VPS `85.239.240.43:3240` (donde el usuario prueba; `MF_ODOO_ENABLED=true`). Deploy = push a `main` + `gh workflow run "Deploy VPS"`.
- Demos aparte en el VPS: stack `jboss-pedidos` y `pedidos-migrado` (26 casos validados contra el JBoss).
- El stack local `127.0.0.1:3240` corre imágenes viejas (descargas poco fiables en el Mac): no sirve para probar lo nuevo.

## En qué iba (últimos días)
- 2026-11-04: contexto Spring XML de JBoss → Spring Boot (`mf/spring_xml.py`: `@ImportResource`, CXF, JMS, rutas XML); cliente SOAP conserva transporte HTTP de CXF 3; `loggingErrorHandler()`; demo migrada desplegada y unida a la red de la API.
- 2026-11-04/05: botón «Relanzar» para trabajos fallidos/vencidos/cancelados (`POST /jobs/{id}/relaunch`).
- 2026-11-05: doc DataStage corregida (IA y programación ya implementadas).
- Árbol limpio, sin trabajo a medio hacer. Ramas `feature/*` remotas ya están fusionadas (salvo `mf/35659167`, PR de prueba).

## Pendiente conocido
- Re-ejecutar una **fase suelta** de un trabajo (hoy solo se relanza entero).
- APEX fase 2 (saltos reales con Oracle + APEX) y fase 3 (ORDS en vivo): faltan instaladores/servidor y esquemas.
- Odoo 20 (sin scripts de OpenUpgrade); SQL no reconstruible queda manual.
- Paginación keyset si crecen volúmenes; NetworkPolicy de modo k8s sin verificar; GitLab/Bitbucket para PR no implementados.
- `vm:`/`direct-vm:` siguen como paso manual bloqueante.

## Reglas de trabajo con este usuario
- Hablar en español. `git pull --rebase` **antes** de empezar cualquier tarea (otra sesión también empuja a `main`).
- Corregir local, probar, push a `main` sin pararse a preguntar; ramas `feature/*` + merge es lo habitual.
- Nunca declarar terminado lo no probado; confianza en niveles, nunca en %. Actualizar `ESTADO_IMPLEMENTACION.md` con evidencia.
- Exploración de código: índice MCP `codebase-memory-mcp` (proyecto `Users-zurisaddaialmanza-Documents-migration-factory`, reindexado hoy).
- Ejecutar código externo (p. ej. OCA odoo-module-migrator en local) requiere aprobación del usuario.

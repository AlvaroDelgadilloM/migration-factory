# API propuesta — /api/v1
OIDC bearer. Respuesta error: `{code,message,requestId,details}`. Listados con cursor y limit máximo; filtros validados. Trabajos largos responden 202 y jobId. Idempotency-Key en creación de jobs; mismo key y distinto payload → 409.

| Método | Ruta | Uso |
|---|---|---|
| POST | /projects | Registrar proyecto |
| GET | /projects | Cartera autorizada |
| GET | /projects/{id} | Detalle |
| POST | /projects/{id}/scans | Analizar commit fijo |
| GET | /scans/{id} | Estado e inventario |
| GET | /scans/{id}/findings | Filtros por regla, severidad y módulo |
| PATCH | /findings/{id}/review | Decisión motivada con If-Match |
| POST | /scans/{id}/plans | Crear plan para perfil versionado |
| GET | /plans/{id} | Pasos y precondiciones |
| POST | /plans/{id}/preview | Generar candidato/diff aislado |
| POST | /plans/{id}/executions | Ejecutar plan aprobado |
| GET | /executions/{id} | Fases, validaciones y enlaces |
| POST | /executions/{id}/cancel | Solicitar cancelación |
| GET | /executions/{id}/events | SSE de progreso; Last-Event-ID |
| GET | /executions/{id}/diff | Diff paginado por archivo |
| POST | /executions/{id}/validations | Ejecutar suites elegidas |
| POST | /executions/{id}/pull-request | PR borrador tras revisión |
| GET | /profiles | Perfiles compatibles validados |
| GET | /rules | Catálogos y reglas |
| GET | /artifacts/{id}/download | Descarga autorizada temporal |
| GET | /audit | Auditoría según permisos |

Ejemplo scan: `{ "commitSha": "SHA_VERIFICADO", "catalogId": "UUID" }`.
Ejemplo plan: `{ "profileId": "UUID", "findingIds": ["UUID"] }`.
Nunca aceptar comandos shell arbitrarios o una URL Git sin validar proveedor/host. SSE incluye solo IDs, progreso y texto redactado.

409: conflicto de estado/versión; 422: parámetros inválidos; 403: permiso; 404: recurso no visible; 429: cuota. OpenAPI generado desde Pydantic será la fuente contractual cuando exista backend. Este documento especifica endpoints; no declara una API ya implementada.


## Estado
Implementada en `backend/mf/api`. La fuente contractual es el OpenAPI generado (`/api/v1/openapi.json`). Endpoints adicionales: `/plans/{id}/approve`, `/executions/{id}/rollback`, `/diff-files/{id}`, `/diff-files/{id}/review`, `/jobs/{id}`, `/jobs/{id}/cancel`, `/jobs/{id}/logs`, `/jobs/{id}/events` (SSE), `/projects/{id}/members`, `/projects/{id}/permissions`, `/projects/{id}/artifacts`, `/audit/export`, `/auth/config`, `/auth/dev-login`, `/me`. `POST /executions/{id}/validations` registra evidencia manual (las suites automáticas corren en cada ejecución).

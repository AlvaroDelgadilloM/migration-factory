# Prompts de implementación por módulo
Usar los documentos como contrato. Implementar incrementalmente; no pedir a IA que genere toda la plataforma y asuma que compila.

## Backend
«Implementa API FastAPI de proyectos, scans y findings usando docs/05_DATOS.md y docs/06_API.md. SQLAlchemy y Alembic, OIDC y autorización por proyecto. Jobs 202 con idempotencia. No ejecutar Maven desde HTTP. Añade pruebas de aislamiento de permisos y conflictos de versión. Entrega comandos de ejecución y limitaciones.»

## Scanner
«Mejora src/scanner.py para módulos Maven y propiedades heredadas mediante análisis seguro. Conserva salida versionada y evidencias archivo/línea; separa observado de resuelto. No sustituyas regex por promesas de AST: incorpora parser y fixtures. Reporta rutas dinámicas como desconocidas. No ejecutes código durante análisis estático.»

## OpenRewrite
«Implementa runner de docs/08_OPENREWRITE.md en worker aislado. Versiones/recetas desde catálogo aprobado. shell=False, timeout y cancelación de grupo; dryRun primero. Produce diff/hash/evidencia sin tocar origen. Prueba fallo parcial, retry e idempotencia. No actualices automáticamente JTA, broker o vm→seda.»

## Frontend
«Implementa Angular standalone, Signals, PrimeNG y rutas lazy según docs/07_FRONTEND.md. Toma mockups/index.html como referencia visual. Incluye estados empty/loading/error y permisos. UI desde DTO OpenAPI; no hardcodear reportes de ejemplo como resultados reales. Diff por archivo y SSE recuperable.»

## Equivalencia
«Diseña pruebas legacy/candidato con mismos contratos y datasets sintéticos. Cubrir JMS ack/redelivery/DLQ, duplicados y rollback DB. Registrar resultados comparables y fallas baseline. No inferir equivalencia solo de compile/test unitario.»

## Revisión IA
«Evalúa este hallazgo y contexto redactado. Devuelve explicación, alternativas, diff propuesto y pruebas necesarias. Declara lo que desconoces. No incluyas porcentajes de confianza, secretos ni acciones Git. Conserva contratos y transacciones; si requiere rediseño indícalo.»

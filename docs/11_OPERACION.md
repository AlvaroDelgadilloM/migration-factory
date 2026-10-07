# Operación y despliegue
## Desarrollo del kit
```bash
python src/scanner.py examples/legacy --output /tmp/report --target spring
python -m unittest discover -s tests -v
```
Salida: `report.json` y `report.html`. Los reportes se crean fuera del repo de origen. El scanner rechaza output dentro del proyecto para mantenerlo sin modificaciones.

## Plataforma futura
Entornos dev/staging/prod separados; UI y API detrás de TLS; PostgreSQL con backup; Redis para jobs; almacenamiento de artefactos con TTL; workers en contenedores efímeros. Variables: DATABASE_URL, REDIS_URL, OIDC_ISSUER, OIDC_AUDIENCE, ARTIFACT_BUCKET, VAULT_REF, MAX_JOB_SECONDS. Secretos inyectados; nunca commiteados.

## CI
Lint/types → tests scanner/API → tests frontend → fixtures recetas → build images → dependency scan → staging. Jobs de recetas utilizan repos piloto, no datos productivos. Pin de imágenes por digest y dependencias en lockfiles.

## Observabilidad
Métricas: cola pendiente, duración por fase, fallos, timeout, hallazgos por regla, porcentaje de suites ejecutadas y minutos de intervención medidos. Log correlacionado por requestId/runId sin código sensible. Alertar pérdida de lease, artefactos no disponibles y cuota de disco.

## Recuperación
Reintentar lectura/transporte con backoff limitado; no reaplicar una receta sin verificar fase/hash. Reinicio del worker consulta estado y descarta workspace incompleto. Restaurar DB y artefactos desde backup probado. Run reproducible requiere acceso al mismo commit, catálogo, perfil y dependencias fijadas.


## Estado
Variables reales con prefijo `MF_` (ver `.env.example` y `backend/mf/config.py`). Arranque: `docker compose up -d --build` (README).

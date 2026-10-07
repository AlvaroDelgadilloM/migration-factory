# Observabilidad y auditoría

## Métricas

- jobs_started_total
- jobs_failed_total
- migration_success_rate
- average_build_time
- recipes_applied_total
- autofix_success_rate
- manual_review_ratio

## Logs estructurados

Cada evento:
- jobId
- phase
- recipeId
- file
- severity
- duration
- result

## Trazabilidad

Registrar:
- commit origen
- hash snapshot
- versión del motor
- versión de reglas
- fecha
- usuario
- target seleccionado

# Pipeline seguro y transaccional

## Flujo recomendado

```text
INGEST
  -> ENV CHECK
  -> SNAPSHOT SOURCE
  -> CREATE BASELINE
  -> POM REPAIR
  -> BASELINE VALIDATE
  -> BASELINE COMPILE
  -> ANALYZE
  -> PLAN
  -> CREATE CANDIDATE
  -> APPLY RECIPE BATCH 1
  -> VALIDATE
  -> APPLY RECIPE BATCH 2
  -> VALIDATE
  -> SECRET REMEDIATION
  -> BUILD
  -> TEST
  -> AUTOFIX
  -> FINAL BUILD
  -> REPORT
```

## Estados

- QUEUED
- RUNNING
- PARTIAL_SUCCESS
- SUCCEEDED
- FAILED
- BLOCKED
- CANCELLED

## Quality gates

No puede ser `SUCCEEDED` si:
- candidate compile falla
- recipe crítica falla
- test obligatorio falla
- secret scan crítico sigue abierto

## Rollback

Cada batch debe registrar:
- snapshot anterior
- archivos modificados
- hash
- recipe
- resultado

## Idempotencia

Ejecutar dos veces el mismo job debe producir el mismo candidate salvo timestamps o metadatos.

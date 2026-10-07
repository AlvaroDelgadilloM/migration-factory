# Reportes

## analysis.json
Inventario machine-readable.

## baseline-report.md
Debe registrar:

- estado del ambiente
- versión Java/Maven
- warnings de Jansi o filesystem
- errores originales del POM
- reparaciones aplicadas
- resultado del baseline build

## migration-plan.md
Antes de modificar, indicar qué se pretende cambiar.

## migration-report.md
Después de la migración:

- archivos creados
- archivos transformados
- dependencias cambiadas
- rutas migradas
- integraciones migradas
- pruebas
- build

## manual-actions.md
Debe ser accionable.

Ejemplo:

```text
## MANUAL-004 - JMS Broker configuration

Source:
OrderRoute.java

Detected:
java:/JmsXA

Reason:
No broker connection URL was found in the repository.

Required:
Define BROKER_URL, username and secret.

Risk:
HIGH
```

## Resumen final

```text
Migration readiness: 82%
Automatic changes: 142
Automatic with review: 18
Manual: 9
Blocked: 1
Build: SUCCESS
Tests: 21/24
```


## Clasificación obligatoria
Cada hallazgo debe indicar una categoría: `ENVIRONMENT`, `BUILD_MODEL`, `DEPENDENCY`, `SOURCE`, `MIGRATION`, `TEST` o `SECURITY`.

Ejemplo:

```text
Category: BUILD_MODEL
Finding: Camel dependency without version
Baseline: FIXED
Migration impact: NONE yet
```

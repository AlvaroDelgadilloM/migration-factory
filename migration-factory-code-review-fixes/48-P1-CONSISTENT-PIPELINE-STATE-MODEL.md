# P1 — Modelo de estados consistente entre API, CLI y workspace

## Objetivo

Evitar que rutas distintas del producto reporten estados incompatibles.

## Problema

El backend, CLI y workspace tienen caminos de ejecución distintos para:

```text
baseline
stubs
candidate model
OpenRewrite
compile
test
report
```

Eso puede causar:

```text
API = PARTIAL_SUCCESS
CLI = SUCCESS
workspace = BLOCKED
```

para el mismo caso.

## Modelo único

Crear un objeto compartido:

```python
MigrationValidationState
```

con:

```text
baselineModel
baselineDependencyResolution
baselineCompile
recipeExecution
candidateModel
candidateDependencyResolution
candidateCompile
candidateTest
stubMode
validationConfidence
primaryReason
secondaryReasons
```

## Reglas

Todos los frontends/orchestrators consumen este mismo objeto.

No inferir `SUCCESS` de un único Maven exit code.

## Estado final

Ejemplo degradado:

```text
migrationExecution=COMPLETED
candidateModel=PASS
candidateDependencyResolution=BLOCKED
candidateCompile=UNVERIFIED
candidateTest=NOT_RUN
overall=PARTIAL_SUCCESS
```

## Criterios de aceptación

- CLI/API/workspace producen mismo estado para la misma ejecución.
- Ningún `BUILD SUCCESS` de un goal auxiliar determina el estado final.

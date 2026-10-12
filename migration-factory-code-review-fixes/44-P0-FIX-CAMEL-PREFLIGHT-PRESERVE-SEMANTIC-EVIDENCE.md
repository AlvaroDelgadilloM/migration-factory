# P0 — Fix: conservar evidencia semántica en Camel preflight

## Problema

El análisis/planning ya construye decisiones ricas con:

```text
ProjectEvidence
DependencyEvidence
usageEvidence
endpoints
packaging
runtime/platform
confidence
```

pero el preflight gate vuelve a ejecutar una decisión usando contexto reducido, por ejemplo:

```python
decide(artifact_id, usage, target_camel)
```

Esto puede destruir información ya inferida.

Caso peligroso:

```text
planning:
camel-cxf
usage=SOAP
runtime=OSGI_BLUEPRINT
evidence=HIGH
```

y después:

```text
preflight:
usage insuficiente
→ CXF_USAGE_AMBIGUOUS
```

## Regla

No recalcular una decisión semántica con menos contexto.

## Opción recomendada

Persistir una estructura:

```python
CamelComponentDecision
```

con:

```python
artifact_id
status
target
project_context
dependency_evidence
usage
configuration_style
artifact_confidence
usage_confidence
migration_confidence
reason_code
```

El preflight debe validar esa decisión, no recrearla.

## Flujo

```text
analysis
  ↓
semantic decision
  ↓
persist decision
  ↓
preflight validates decision
  ↓
artifact availability
  ↓
POM write
```

No:

```text
analysis decision
  ↓
discard context
  ↓
preflight decide again
```

## Si es necesario recalcular

Debe utilizar exactamente el mismo contexto:

```python
decide(
    artifact_id=...,
    usage=...,
    target_camel=...,
    project_context=...,
    dependency_evidence=...,
    endpoints=...,
    packaging=...,
    platform=...
)
```

## Criterios de aceptación

- CXF SOAP detectado no puede volverse ambiguo en preflight sin evidencia nueva.
- `DependencyEvidence` debe sobrevivir hasta el gate final.
- Confidence no debe aumentar ni bajar sin razón registrada.
- Planning y preflight deben emitir la misma decisión semántica para el mismo snapshot.

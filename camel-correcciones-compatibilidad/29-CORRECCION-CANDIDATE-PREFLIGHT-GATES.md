# Corrección: Candidate Preflight Gates antes de compile

## Objetivo

Consolidar gates antes de ejecutar `mvn compile`.

## Orden

```text
POM_SANITY_GATE
    ↓
CAMEL_COMPONENT_COMPATIBILITY_GATE
    ↓
CAMEL_VERSION_CONSISTENCY_GATE
    ↓
ARTIFACT_AVAILABILITY_GATE
    ↓
DEPENDENCY_VALIDATION_GATE
    ↓
CANDIDATE_COMPILE
```

## Reglas

Si cualquier gate crítico falla:

```text
candidate compile = SKIPPED
```

y debe registrarse:

```text
BLOCKED_BY_PREFLIGHT_GATE
```

## Ejemplo

```text
POM_SANITY_GATE: PASS
CAMEL_COMPONENT_COMPATIBILITY_GATE: FAIL
ARTIFACT_AVAILABILITY_GATE: SKIPPED
CANDIDATE_COMPILE: SKIPPED
```

## Criterios de aceptación

- No compilar candidates conocidos como inválidos.
- El primer fallo crítico debe quedar como causa primaria.
- Los demás pasos deben figurar como SKIPPED, no FAILED.

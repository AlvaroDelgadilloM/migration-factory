# Implementación: orden completo recomendado

## Pipeline objetivo

```text
SOURCE
  ↓
REPOSITORY DISCOVERY
  ↓
RUNTIME DETECTION
  ↓
BASELINE REPAIR
  ↓
OFFICIAL CAMEL RECIPE
  ↓
POM NORMALIZATION
  ↓
CUSTOM COMPATIBILITY MAPPING
  ↓
POM DEDUPLICATION
  ↓
POM SANITY GATE
  ↓
CAMEL VERSION CONSISTENCY GATE
  ↓
ARTIFACT AVAILABILITY GATE
  ↓
DEPENDENCY VALIDATION GATE
  ↓
CANDIDATE COMPILE
  ↓
COMPILE ERROR CLASSIFIER
  ↓
MULTI-ROUND AUTOFIX
  ↓
TEST
  ↓
REPORT
```

## Resultado esperado para member-phygital

Antes de compile, el sistema debe detectar:

```text
- bundle packaging
- OSGi runtime
- Camel version conflicts
- invalid Swagger artifacts
- duplicate OpenAPI dependency
```

Solo si los gates pasan debe ejecutar Maven compile.

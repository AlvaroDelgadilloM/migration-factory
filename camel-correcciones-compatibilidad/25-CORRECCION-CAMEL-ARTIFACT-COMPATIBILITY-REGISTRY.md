# Corrección: Camel Artifact Compatibility Registry

## Objetivo

Evitar el version bump ciego de componentes que fueron removidos, renombrados o reemplazados en Camel 4.

## Estados

```text
SUPPORTED
RENAMED
REPLACED
REMOVED
ARCHITECTURAL_MIGRATION
MANUAL_REVIEW
```

## Ejemplos

```yaml
camel-swagger-java:
  status: REPLACED
  target: camel-openapi-java
  confidence: HIGH

camel-swagger:
  status: MANUAL_REVIEW
  action: inspect_usage

camel-blueprint:
  status: ARCHITECTURAL_MIGRATION
  targetRuntime:
    - spring-boot
    - quarkus

camel-cxf:
  status: MANUAL_REVIEW
  action: validate_target_artifacts_and_usage
```

## Regla

Nunca hacer:

```text
artifact:X.Y
→
same artifact:4.14.0
```

sin consultar el registry.

## Flujo

```text
SOURCE ARTIFACT
    ↓
REGISTRY
    ↓
CLASSIFY
    ↓
REPLACE / REMOVE / MANUAL / ARCHITECTURAL
    ↓
VALIDATE TARGET ARTIFACT
```

## Resultado

```json
{
  "source": "camel-swagger-java",
  "status": "REPLACED",
  "target": "camel-openapi-java",
  "confidence": "HIGH"
}
```

## Criterios de aceptación

- Un artifact REMOVED nunca debe aparecer en el target POM.
- Un artifact REPLACED debe usar el target correcto.
- Un artifact MANUAL_REVIEW bloquea autofix destructivo.

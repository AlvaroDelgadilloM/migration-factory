# Corrección: reporte de causa raíz

## Objetivo

Evitar mensajes demasiado genéricos como:

```text
MANDATORY_RECIPE_NOT_APPLIED
```

cuando existe una causa técnica concreta.

## Prioridad de causas

```text
1. ORCHESTRATION_ERROR
2. POM_SANITY_ERROR
3. CAMEL_VERSION_CONFLICT
4. INVALID_CAMEL_COMPONENT_MAPPING
5. UNSUPPORTED_TARGET_ARTIFACT
6. DEPENDENCY_RESOLUTION_ERROR
7. JAVA_COMPILATION_ERROR
8. TEST_FAILURE
9. MANUAL_ACTIONS_PENDING
```

## Ejemplo

Incorrecto:

```text
FAILED
reason=MANDATORY_RECIPE_NOT_APPLIED
```

Correcto:

```text
FAILED

primaryReason:
INVALID_CAMEL_COMPONENT_MAPPING

artifact:
camel-swagger-java

target:
Camel 4

recommendedAction:
REPLACE_WITH camel-openapi-java
```

## Criterios de aceptación

- El reporte debe exponer causa primaria y causas secundarias.
- Los warnings no deben desplazar la causa raíz.
- Los errores de infraestructura deben diferenciarse de los del código.

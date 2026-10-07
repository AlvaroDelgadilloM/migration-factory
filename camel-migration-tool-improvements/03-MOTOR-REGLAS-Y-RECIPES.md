# Motor de reglas y recipes

## Objetivo

Separar la lógica de migración del código del motor.

## Regla declarativa

```yaml
id: CAMEL_HTTP4_TO_HTTP
source:
  artifact: org.apache.camel:camel-http4
target:
  artifact: org.apache.camel:camel-http
conditions:
  targetCamel: ">=4"
actions:
  - replaceDependency
  - rewriteEndpointScheme
confidence: 0.98
rollback: true
```

## Tipos de reglas

- dependency
- import
- annotation
- endpoint
- xml
- config
- build-plugin
- runtime
- security

## Orden de ejecución

1. preflight
2. baseline repair
3. Java runtime migration
4. javax/jakarta
5. Camel core
6. Camel components
7. runtime migration
8. config externalization
9. security cleanup
10. compile fixes

## Reglas obligatorias

- No ejecutar recipes si `mvn validate` falla.
- Cada recipe debe tener snapshot previo.
- Si falla una recipe crítica: rollback.
- Recipes opcionales pueden quedar en warning.

## Mejoras

- dependencia entre reglas
- prioridad
- rollback individual
- dry-run
- diff preview
- confidence threshold configurable

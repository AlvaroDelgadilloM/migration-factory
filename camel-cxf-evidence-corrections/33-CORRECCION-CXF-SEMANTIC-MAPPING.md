# Corrección: mapeo semántico de camel-cxf

## Objetivo
Eliminar la regla universal `camel-cxf -> camel-cxf-soap`.

## Nuevo comportamiento
```text
Target status: ARCHITECTURAL_MAPPING
Target: DETECT_BY_USAGE
Auto migration: CONDITIONAL
```

## Analizador
Crear `CxfUsageAnalyzer` y detectar:
- SOAP
- REST
- SPRING_XML
- BLUEPRINT
- JAVA_DSL
- JAX_WS
- JAX_RS
- TRANSPORT
- UNKNOWN

## Reglas
```text
SOAP + Spring XML -> camel-cxf-spring-soap
SOAP -> camel-cxf-soap
REST + Spring XML -> camel-cxf-spring-rest
REST -> camel-cxf-rest
```

Si hay Blueprint/OSGi, la migración de runtime sigue siendo obligatoria y no debe marcarse `Auto migration: YES`.

## Flujo
```text
camel-cxf
  ↓
analyze usage
  ↓
analyze runtime/config
  ↓
resolve semantic target
  ↓
validate target artifact
  ↓
write POM
```

## Criterios de aceptación
- Uso ambiguo -> `MANUAL_REVIEW`.
- Blueprint detectado -> `CONDITIONAL` o `NO`.
- Nunca mapear a `camel-cxf-soap` solo por el artifactId.

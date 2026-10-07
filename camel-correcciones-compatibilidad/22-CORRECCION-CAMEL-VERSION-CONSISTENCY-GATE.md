# Corrección: Camel Version Consistency Gate

## Objetivo

Evitar que un candidate mezcle dependencias de distintas versiones mayores de Apache Camel.

Ejemplo problemático:

```text
camel-core:4.14.x
camel-openapi-java:2.23.2
camel-cxf-soap:2.23.2
```

El candidate debe bloquearse antes del build si existe mezcla de major versions.

## Regla

Recolectar todas las dependencias:

```text
groupId = org.apache.camel
```

Resolver versiones efectivas desde:

- dependency version
- dependencyManagement
- BOM
- properties
- parent
- imported BOMs

Normalizar a:

```text
artifactId
resolvedVersion
majorVersion
source
```

## Gate

```text
CAMEL_VERSION_CONSISTENCY_GATE
```

Estados:

```text
PASS
WARN
FAIL
```

Regla principal:

```text
si targetMajor = 4
y existe una dependencia Camel con major != 4
→ FAIL
```

Excepción:

- artefactos auxiliares no versionados por Camel BOM solo si están explícitamente clasificados como compatibles.

## Resultado esperado

```json
{
  "status": "FAIL",
  "reason": "CAMEL_VERSION_CONFLICT",
  "targetMajor": 4,
  "conflicts": [
    {
      "artifact": "camel-cxf-soap",
      "version": "2.23.2"
    },
    {
      "artifact": "camel-openapi-java",
      "version": "2.23.2"
    }
  ]
}
```

## Pipeline

```text
POM TRANSFORM
   ↓
RESOLVE EFFECTIVE DEPENDENCIES
   ↓
CAMEL VERSION CONSISTENCY GATE
   ↓
PASS → continue
FAIL → block candidate build
```

## Criterios de aceptación

- Si todas las dependencias Camel son 4.x → PASS.
- Si hay mezcla 2.x / 4.x → FAIL.
- Si no puede resolver versión → WARN o FAIL según criticidad.
- El build no debe ejecutarse si el gate falla.

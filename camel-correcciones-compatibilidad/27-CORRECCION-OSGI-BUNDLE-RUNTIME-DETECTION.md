# Corrección: detección de OSGi / bundle antes de migrar

## Objetivo

Detectar que un proyecto no es solo Camel, sino también OSGi/Karaf/Blueprint.

Señales:

```text
<packaging>bundle</packaging>
maven-bundle-plugin
OSGI-INF/blueprint
Bundle-SymbolicName
Import-Package
Export-Package
```

## Clasificación

```text
OSGI_PROJECT = true
```

## Regla

Si target runtime es Spring Boot:

```text
bundle
→ jar
```

y revisar:

```text
Blueprint beans
OSGi service references
CamelContext Blueprint
MANIFEST metadata
bundle plugin
```

No aplicar únicamente:

```text
Camel 2/3 → Camel 4
```

## Nuevo finding

```text
TARGET_RUNTIME_MIGRATION_REQUIRED
```

## Resultado esperado

```json
{
  "sourceRuntime": "OSGI_BLUEPRINT",
  "targetRuntime": "SPRING_BOOT",
  "status": "ARCHITECTURAL_MIGRATION"
}
```

## Criterios de aceptación

- `packaging=bundle` debe activar detección OSGi.
- `camel-blueprint` no debe recibir version bump.
- Debe generarse plan de migración de runtime.

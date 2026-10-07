# Corrección: Artifact Availability Gate

## Objetivo

Comprobar que cada dependencia objetivo exista y sea resoluble antes de escribir el POM final o ejecutar compile.

## Gate

```text
ARTIFACT_AVAILABILITY_GATE
```

Validar:

- groupId
- artifactId
- version
- repositorios permitidos
- credenciales/repo policy
- existencia real
- compatibilidad con BOM

## Estados

```text
PASS
NOT_FOUND
REPOSITORY_BLOCKED
AUTH_REQUIRED
VERSION_CONFLICT
```

## Flujo

```text
PROPOSED TARGET DEPENDENCY
       ↓
REGISTRY CHECK
       ↓
REPOSITORY RESOLUTION CHECK
       ↓
AVAILABILITY GATE
       ↓
WRITE POM
```

## Regla

No dejar que Maven compile sea el primer detector de artifact inexistente.

## Criterios de aceptación

- `camel-swagger-java:4.x` inexistente debe bloquearse antes de compile.
- El reporte debe indicar artifact y regla aplicada.
- Si el repo corporativo bloquea un artifact existente, clasificarlo distinto de NOT_FOUND.

# Execution Sandbox

## Objetivo

Ejecutar builds de proyectos desconocidos sin comprometer el host.

## Requisitos

- contenedor aislado por job
- CPU/memoria limitadas
- timeout
- red controlada
- filesystem temporal
- usuario no root
- sin Docker socket
- workdir separado

## Maven

Configurar:

```bash
MAVEN_OPTS="-Djansi.tmpdir=/work/tmp/jansi -Djansi.force=false"
```

## Red

Modos:
- offline
- Maven Central only
- allowlist corporativa

## Limpieza

Eliminar workspace después del job salvo artefactos y reportes permitidos.

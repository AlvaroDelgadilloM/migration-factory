# Corrección: workspace multiproyecto sin POM agregador

## Objetivo

Corregir el caso en el que un repositorio multiproyecto es detectado correctamente, pero el orquestador intenta ejecutar Maven sobre el `workspaceRoot` como si existiera un `pom.xml` agregador.

Error observado:

```text
MISSING_CANDIDATE_POM:
no existe /work/.../attempt-1/source/develop/pom.xml

CANDIDATE_BUILD_CONFIGURATION_ERROR
reason=MAVEN_PROJECT_NOT_FOUND
```

Este error no significa necesariamente que falte un archivo que debería existir.

En un repositorio del tipo:

```text
MULTI_PROJECT_REPOSITORY
```

puede ser completamente válido que el workspace no tenga un `pom.xml` raíz.

---

# 1. Estructura válida

Ejemplo:

```text
develop/
└── commons/
    ├── common-activemq/
    │   └── pom.xml
    ├── common-catalogue/
    │   └── pom.xml
    ├── common-datasource/
    │   └── pom.xml
    ├── common-epay/
    │   └── pom.xml
    └── common-rate-exchange/
        └── pom.xml
```

No existe:

```text
develop/pom.xml
```

ni necesariamente:

```text
develop/commons/pom.xml
```

Esto es válido si los proyectos son Maven independientes dentro del mismo workspace.

---

# 2. Problema actual

El orquestador está usando algo equivalente a:

```python
candidate_pom = workspace_root / "pom.xml"
```

y después:

```bash
mvn -f /work/.../source/develop/pom.xml clean verify
```

Eso provoca:

```text
MissingProjectException
```

o:

```text
MISSING_CANDIDATE_POM
```

aunque los POM reales sí existan en los módulos.

---

# 3. Diferenciar WorkspaceContext y ProjectContext

Debe existir una separación estricta.

## WorkspaceContext

Representa el conjunto:

```python
class WorkspaceContext:
    workspace_root: Path
    repository_type: str
    projects: list[ProjectContext]
    aggregator_pom: Path | None
```

Ejemplo:

```text
workspaceRoot:
source/develop
```

No implica:

```text
source/develop/pom.xml
```

---

## ProjectContext

Representa un proyecto Maven compilable:

```python
class ProjectContext:
    name: str
    root: Path
    pom: Path
    baseline_root: Path
    baseline_pom: Path
    candidate_root: Path
    candidate_pom: Path
```

Ejemplo:

```text
name:
common-activemq

candidateRoot:
projects/common-activemq/candidate

candidatePom:
projects/common-activemq/candidate/pom.xml
```

---

# 4. Regla principal

Nunca inferir:

```text
workspaceRoot + /pom.xml
```

como target de build.

El build target debe resolverse según el tipo de contexto.

---

# 5. BuildTargetResolver

Crear:

```text
build/
└── build_target_resolver.py
```

Ejemplo:

```python
def resolve_build_target(context):

    if isinstance(context, ProjectContext):
        return BuildTarget(
            type="PROJECT",
            pom=context.candidate_pom
        )

    if isinstance(context, WorkspaceContext):

        if context.repository_type == "MAVEN_MULTI_MODULE":
            if context.aggregator_pom:
                return BuildTarget(
                    type="WORKSPACE_AGGREGATOR",
                    pom=context.aggregator_pom
                )

        if context.repository_type == "MULTI_PROJECT_REPOSITORY":
            return BuildTarget(
                type="NOT_APPLICABLE",
                pom=None
            )

    return BuildTarget(
        type="INVALID",
        pom=None
    )
```

---

# 6. Comportamiento por tipo de repositorio

## SINGLE_PROJECT

```text
build project root
```

Ejemplo:

```bash
mvn -f candidate/pom.xml clean verify
```

---

## MAVEN_MULTI_MODULE

Existe POM agregador.

Ejemplo:

```text
root/
├── pom.xml
├── module-a/pom.xml
└── module-b/pom.xml
```

Se permite:

```bash
mvn -f root/pom.xml clean verify
```

Opcionalmente también builds individuales.

---

## MULTI_PROJECT_REPOSITORY

No existe agregador.

Nunca ejecutar:

```bash
mvn -f workspaceRoot/pom.xml ...
```

Ejecutar por proyecto:

```bash
mvn -f projects/common-activemq/candidate/pom.xml clean verify
mvn -f projects/common-catalogue/candidate/pom.xml clean verify
mvn -f projects/common-datasource/candidate/pom.xml clean verify
```

---

# 7. Workspace build status

Para un `MULTI_PROJECT_REPOSITORY`:

```text
Workspace Maven Build:
SKIPPED
```

Razón:

```text
WORKSPACE_HAS_NO_AGGREGATOR_POM
```

o:

```text
WORKSPACE_BUILD_NOT_APPLICABLE
```

No usar:

```text
FAILED
```

porque no hay un error.

---

# 8. MavenExecutionGuard

Antes de ejecutar Maven sobre un proyecto:

```python
def validate_project_build_target(project):

    if not project.candidate_root.exists():
        return GuardResult(
            ok=False,
            reason="MISSING_CANDIDATE_DIRECTORY"
        )

    if not project.candidate_pom.exists():
        return GuardResult(
            ok=False,
            reason="MISSING_CANDIDATE_POM"
        )

    return GuardResult(ok=True)
```

Aplicar esto solo a `ProjectContext`.

---

# 9. No aplicar MavenExecutionGuard de proyecto al workspace

Incorrecto:

```python
validate_project_build_target(workspace)
```

Correcto:

```python
if workspace.repository_type == "MULTI_PROJECT_REPOSITORY":
    skip_workspace_build()

    for project in workspace.projects:
        validate_project_build_target(project)
        build_project(project)
```

---

# 10. Orquestación correcta

```text
WorkspaceContext
        ↓
repositoryType?
        ↓
┌──────────────────────────────┐
│ SINGLE_PROJECT               │
│ build project                │
├──────────────────────────────┤
│ MAVEN_MULTI_MODULE           │
│ build aggregator             │
├──────────────────────────────┤
│ MULTI_PROJECT_REPOSITORY     │
│ skip workspace build         │
│ build each ProjectContext    │
└──────────────────────────────┘
```

---

# 11. Integración con migración por oleadas

En cada wave:

```python
for project in wave.runnable_projects:

    guard = validate_project_build_target(project)

    if not guard.ok:
        register_project_failure(
            project,
            guard.reason
        )
        continue

    result = run_maven(
        pom=project.candidate_pom,
        goals=["clean", "verify"]
    )

    register_project_result(
        project,
        result
    )
```

No ejecutar Maven sobre el workspace.

---

# 12. Ejemplo correcto

Workspace:

```text
develop/commons
```

Projects:

```text
common-activemq
common-catalogue
common-datasource
common-epay
common-rate-exchange
```

Build:

```text
Workspace build:
SKIPPED

Reason:
WORKSPACE_BUILD_NOT_APPLICABLE
```

Luego:

```text
common-activemq
mvn -f .../common-activemq/candidate/pom.xml clean verify

common-catalogue
mvn -f .../common-catalogue/candidate/pom.xml clean verify

common-datasource
mvn -f .../common-datasource/candidate/pom.xml clean verify
```

---

# 13. Logs recomendados

```text
[workspace-build]
repositoryType=MULTI_PROJECT_REPOSITORY
status=SKIPPED
reason=WORKSPACE_BUILD_NOT_APPLICABLE
```

Luego:

```text
[candidate-build][common-activemq]
pom=/work/.../projects/common-activemq/candidate/pom.xml
status=RUNNING
```

---

# 14. Clasificación correcta de errores

Si falta el POM de un proyecto:

```text
MISSING_CANDIDATE_POM
```

Eso sí es un error de proyecto/migrador.

Si no existe POM del workspace multiproyecto:

```text
WORKSPACE_BUILD_NOT_APPLICABLE
```

Eso no es error.

---

# 15. MissingProjectException

Si Maven devuelve:

```text
MissingProjectException
```

el sistema debe revisar primero:

```text
¿se ejecutó Maven sobre un ProjectContext válido?
```

Si no:

```text
classification:
ORCHESTRATION_ERROR
```

Reason:

```text
MAVEN_EXECUTED_ON_NON_PROJECT_ROOT
```

No clasificarlo como:

```text
SOURCE_CODE_ERROR
```

---

# 16. Estado final del workspace

Ejemplo:

```text
Workspace build:
SKIPPED

Projects:
common-activemq      PASS
common-catalogue     PASS
common-datasource    PASS
common-epay          FAIL
common-rate-exchange PASS
```

Resultado:

```text
WORKSPACE_STATUS = PARTIAL_SUCCESS
```

No:

```text
FAILED
```

solo porque no existe `develop/pom.xml`.

---

# 17. No crear POM agregador artificial

No corregir esto creando automáticamente:

```text
develop/pom.xml
```

o:

```text
develop/commons/pom.xml
```

Esto podría alterar la arquitectura real del repositorio.

El sistema debe respetar la estructura existente.

---

# 18. Reporte esperado

```text
Repository type:
MULTI_PROJECT_REPOSITORY

Workspace:
develop

Aggregator POM:
NOT PRESENT

Workspace Maven build:
NOT APPLICABLE

Projects discovered:
5

Projects built:
5
```

---

# 19. Criterios de aceptación

## Caso 1

`MULTI_PROJECT_REPOSITORY` sin POM raíz.

Resultado:

```text
workspace build = SKIPPED
```

PASS.

---

## Caso 2

Cinco proyectos con POM propio.

Resultado:

```text
project builds attempted = 5
```

PASS.

---

## Caso 3

No debe buscar:

```text
workspaceRoot/pom.xml
```

PASS.

---

## Caso 4

Maven debe usar:

```text
ProjectContext.candidatePom
```

PASS.

---

## Caso 5

Si un módulo carece de candidate POM:

```text
MISSING_CANDIDATE_POM
```

solo para ese módulo.

PASS.

---

## Caso 6

Un módulo falla y los demás continúan según waves/dependencias.

PASS.

---

# 20. Arquitectura final

```text
WorkspaceDiscovery
        ↓
RepositoryClassifier
        ↓
MultiProjectOrchestrator
        ↓
WavePlanner
        ↓
ProjectContext
        ↓
BuildTargetResolver
        ↓
MavenExecutionGuard
        ↓
MavenBuilder
```

El `WorkspaceContext` coordina.

El `ProjectContext` compila.

---

# 21. Prioridad

```text
CRITICAL
```

Razón:

El sistema ya detecta repositorios multiproyecto, pero mientras intente ejecutar Maven sobre el `workspaceRoot` sin agregador seguirá produciendo falsos errores `MISSING_CANDIDATE_POM` y `MissingProjectException`.

La regla definitiva es:

```text
MULTI_PROJECT_REPOSITORY
≠
MAVEN_MULTI_MODULE
```

y por tanto:

```text
workspaceRoot
≠
projectRoot
```

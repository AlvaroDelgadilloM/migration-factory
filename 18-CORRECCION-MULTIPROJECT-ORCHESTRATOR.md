# Corrección: MultiProjectOrchestrator para analizar todos los módulos del workspace

## Objetivo

Corregir el caso donde el sistema detecta correctamente múltiples proyectos Maven dentro de un workspace, pero posteriormente ejecuta el análisis sobre un solo proyecto.

## Problema actual

Ejemplo:

```text
develop/commons/
├── common-activemq/pom.xml
├── common-catalogue/pom.xml
├── common-datasource/pom.xml
├── common-epay/pom.xml
└── common-rate-exchange/pom.xml
```

El discovery detecta los 5 proyectos, pero el pipeline termina haciendo algo equivalente a:

```python
project = projects[0]
analyze_project(project)
```

o:

```python
for pom in poms:
    project = parse_project(pom)

return project
```

Esto provoca:

```text
Projects discovered: 5
Projects analyzed: 1
```

## Arquitectura correcta

Debe pasar de:

```text
Workspace discovery
        ↓
Single project pipeline
```

a:

```text
Workspace discovery
        ↓
MultiProjectOrchestrator
        ↓
Project pipelines
        ↓
Workspace aggregator
```

## Nuevo componente

Crear:

```text
orchestration/
└── multi_project_orchestrator.py
```

Responsabilidades:

- recibir el `WorkspaceContext`;
- recorrer todos los `ProjectContext`;
- ejecutar análisis por proyecto;
- guardar resultados aislados;
- construir el grafo de dependencias internas;
- agregar findings comunes;
- calcular orden de migración;
- calcular waves;
- controlar fallos parciales;
- generar reporte global.

## Modelo de contexto

No utilizar únicamente:

```python
context.project
```

Usar:

```python
context.projects
```

Modelo recomendado:

```python
class WorkspaceContext:
    workspace_root: Path
    projects: list[ProjectContext]
    dependency_graph: DependencyGraph
    shared_findings: list[Finding]
    analysis_status: str
    migration_status: str
```

Cada proyecto:

```python
class ProjectContext:
    name: str
    root: Path
    pom: Path
    group_id: str
    artifact_id: str
    version: str
    packaging: str
    technologies: list[str]
    integrations: list
    findings: list
    dependencies: list
    analysis_status: str
    migration_status: str
```

## Discovery completo

Debe devolver una lista:

```python
def discover_projects(workspace_root):
    projects = []

    for pom in find_all_poms(workspace_root):
        projects.append(parse_project(pom))

    return projects
```

No debe devolver solo el último o el primero.

## Análisis de todos los proyectos

```python
for project in workspace.projects:
    analyze_project(project)
```

Cada proyecto ejecuta:

```text
PROJECT ANALYSIS
    ↓
preflight
    ↓
pom analysis
    ↓
java analysis
    ↓
camel analysis
    ↓
spring analysis
    ↓
jms analysis
    ↓
integration analysis
    ↓
code smell analysis
    ↓
project report
```

## Pipeline completo

```text
DISCOVER WORKSPACE
        ↓
DISCOVER PROJECTS
        ↓
BUILD PROJECT CONTEXTS
        ↓
BUILD INTERNAL DEPENDENCY GRAPH
        ↓
MULTI PROJECT ANALYSIS
        ↓
AGGREGATE RESULTS
        ↓
WORKSPACE REPORT
        ↓
CALCULATE MIGRATION WAVES
```

## Análisis paralelo

El análisis puede ejecutarse en paralelo porque es principalmente de lectura.

```python
with ThreadPoolExecutor(max_workers=4) as executor:
    futures = {
        executor.submit(analyze_project, project): project
        for project in workspace.projects
    }

    for future in as_completed(futures):
        project = futures[future]
        result = future.result()
        save_project_result(project, result)
```

El paralelismo debe ser configurable.

## Separar analysis order de migration order

El análisis puede correr sobre todos los proyectos.

La migración debe respetar el grafo de dependencias internas.

```text
ANALYSIS
todos los proyectos

MIGRATION
según dependency graph
```

## Grafo de dependencias internas

Si un proyecto declara:

```xml
<dependency>
    <groupId>com.posadas.common</groupId>
    <artifactId>common-datasource</artifactId>
</dependency>
```

y `common-datasource` existe dentro del workspace, registrar:

```text
INTERNAL_DEPENDENCY
```

Modelo:

```json
{
  "nodes": [
    "common-activemq",
    "common-catalogue",
    "common-datasource",
    "common-epay",
    "common-rate-exchange"
  ],
  "edges": [
    {
      "from": "common-epay",
      "to": "common-datasource"
    }
  ]
}
```

## Orden de migración

Aplicar topological sort y generar waves.

Ejemplo:

```text
Wave 1
- common-datasource
- common-activemq

Wave 2
- common-catalogue
- common-rate-exchange

Wave 3
- common-epay
```

## Estructura de ejecución

No reutilizar una única carpeta mutable:

```text
attempt-1/
├── source/
├── baseline/
└── candidate/
```

Para multiproyecto usar:

```text
attempt-1/
├── source/
├── projects/
│   ├── common-activemq/
│   │   ├── baseline/
│   │   ├── candidate/
│   │   ├── reports/
│   │   └── logs/
│   ├── common-catalogue/
│   ├── common-datasource/
│   ├── common-epay/
│   └── common-rate-exchange/
└── workspace/
    ├── workspace-report.md
    ├── dependency-graph.json
    ├── migration-order.json
    └── shared-findings.json
```

## Estados por proyecto

Estados recomendados:

```text
PENDING
RUNNING
PASS
WARN
FAIL
BLOCKED
SKIPPED
```

Ejemplo:

```text
common-activemq      PASS
common-catalogue     PASS
common-datasource    FAIL
common-epay          PASS
common-rate-exchange PASS
```

## Estado global del workspace

Reglas:

```text
todos PASS
→ SUCCEEDED

alguno FAIL pero el resto fue analizado
→ PARTIAL_SUCCESS

fallo estructural del workspace
→ FAILED

bloqueo por dependencia crítica
→ BLOCKED
```

## No abortar todo por un proyecto

Si:

```text
common-datasource = FAIL
```

el sistema debe continuar analizando los otros módulos.

Durante migración sí puede bloquear dependientes.

Ejemplo:

```text
common-datasource = FAIL
common-epay = BLOCKED_BY_DEPENDENCY
```

Modelo:

```json
{
  "project": "common-epay",
  "status": "BLOCKED",
  "reason": "DEPENDENCY_MIGRATION_FAILED",
  "dependency": "common-datasource"
}
```

## Reporte por proyecto

Generar:

```text
analysis/
├── common-activemq/
│   ├── analysis.json
│   └── report.md
├── common-catalogue/
│   ├── analysis.json
│   └── report.md
├── common-datasource/
│   ├── analysis.json
│   └── report.md
├── common-epay/
│   ├── analysis.json
│   └── report.md
└── common-rate-exchange/
    ├── analysis.json
    └── report.md
```

## Reporte agregado

Ejemplo:

```text
Workspace:
develop/commons

Projects discovered:
5

Projects analyzed:
5

Projects passed:
4

Projects failed:
1

Internal dependencies:
6

Dependency cycles:
0

Migration waves:
3
```

## Findings agregados

Ejemplo:

```text
Java 8       5/5
Camel 2      4/5
javax.jms    3/5
Blueprint    2/5
ActiveMQ     2/5
```

## Shared findings

Archivo:

```text
shared-findings.json
```

Ejemplo:

```json
[
  {
    "rule": "JAVA8_WORKSPACE",
    "affectedProjects": 5,
    "totalProjects": 5
  },
  {
    "rule": "CAMEL2_WORKSPACE",
    "affectedProjects": 4,
    "totalProjects": 5
  }
]
```

## Logging

Todos los logs deben incluir `projectId`.

Ejemplo:

```text
[analysis][common-activemq] scanning pom
[analysis][common-catalogue] PASS
[analysis][common-datasource] FAIL
```

Usar:

```text
workspaceId
projectId
attemptId
```

como correlation IDs.

## Migración por waves

```python
waves = dependency_graph.calculate_waves()

for wave in waves:
    results = migrate_projects_parallel(wave)

    failed = [
        r.project
        for r in results
        if r.status == "FAIL"
    ]

    dependency_graph.block_dependents(failed)
```

## No compartir un ProjectContext mutable

Evitar:

```python
context.project = project
```

dentro de un loop global.

Preferir pasar siempre:

```python
analyze_project(project_context)
```

## No compartir candidate

Incorrecto:

```text
candidate/
```

Correcto:

```text
projects/common-activemq/candidate/
projects/common-catalogue/candidate/
projects/common-datasource/candidate/
```

Esto evita colisiones de `pom.xml`, `target/`, logs y reportes.

## Errores aislados

Un fallo Maven de un módulo debe producir:

```text
PROJECT_BUILD_FAILED
```

y no:

```text
WORKSPACE_BUILD_FAILED
```

salvo que la política del workspace exija que todos los módulos sean válidos.

## Criterios de aceptación

### Caso 1

Workspace con cinco POM independientes:

```text
projectsDiscovered = 5
projectsAnalyzed = 5
```

PASS.

### Caso 2

Un proyecto falla:

```text
projectsAnalyzed = 5
projectsFailed = 1
workspaceStatus = PARTIAL_SUCCESS
```

PASS.

### Caso 3

Existen dependencias internas:

```text
dependency-graph.json
migration-order.json
waves.json
```

PASS.

### Caso 4

Un módulo depende de otro cuya migración falla:

```text
BLOCKED_BY_DEPENDENCY
```

PASS.

### Caso 5

Cada proyecto genera reporte independiente.

PASS.

## Resultado esperado para el caso actual

Entrada:

```text
develop/commons/common-activemq/pom.xml
develop/commons/common-catalogue/pom.xml
develop/commons/common-datasource/pom.xml
develop/commons/common-epay/pom.xml
develop/commons/common-rate-exchange/pom.xml
```

Salida:

```text
Workspace discovered:
develop/commons

Projects discovered:
5

Projects analyzed:
5
```

Nunca:

```text
Projects discovered:
5
Projects analyzed:
1
```

## Prioridad

```text
CRITICAL
```

El discovery multiproyecto ya funciona. La pieza faltante es el:

```text
MultiProjectOrchestrator
```

que debe coordinar todos los `ProjectContext` cuando:

```text
repositoryType = MULTI_PROJECT_REPOSITORY
```

# Corrección: análisis y migración por oleadas en repositorios multiproyecto

## Objetivo

Corregir la orquestación de repositorios multiproyecto para que tanto el análisis como la migración se ejecuten por oleadas (`waves`) respetando dependencias internas, sin detener todo el workspace por un único fallo.

## 1. Dos tipos de oleadas

El sistema debe manejar por separado:

```text
ANALYSIS_WAVES
MIGRATION_WAVES
```

No deben confundirse.

### Analysis waves

Sirven para analizar módulos base primero y propagar contexto a proyectos dependientes.

Ejemplo:

```text
Wave A1
- common-datasource
- common-activemq

Wave A2
- common-catalogue
- common-rate-exchange

Wave A3
- common-epay
```

### Migration waves

Sirven para migrar respetando el grafo de dependencias:

```text
Wave M1
- common-datasource
- common-activemq

Wave M2
- common-catalogue
- common-rate-exchange

Wave M3
- common-epay
```

## 2. Grafo de dependencias

Antes de generar waves, construir el grafo interno con `groupId + artifactId + version`.

Ejemplo:

```text
common-datasource ───────► common-epay
common-activemq ─────────► common-catalogue
common-rate-exchange ────► common-epay
```

## 3. Cálculo de waves

Usar orden topológico por niveles.

```python
def calculate_waves(graph):
    indegree = graph.calculate_indegree()
    remaining = set(graph.nodes)
    waves = []

    while remaining:
        current = [
            node for node in remaining
            if indegree[node] == 0
        ]

        if not current:
            raise DependencyCycleError()

        waves.append(current)

        for node in current:
            remaining.remove(node)
            for dependent in graph.dependents(node):
                indegree[dependent] -= 1

    return waves
```

## 4. AnalysisWaveOrchestrator

Crear:

```text
orchestration/
└── analysis_wave_orchestrator.py
```

Responsabilidades:

- calcular analysis waves;
- ejecutar análisis por wave;
- paralelizar proyectos dentro de una wave;
- persistir resultados;
- propagar contexto a dependientes;
- permitir análisis parcial;
- generar findings agregados por wave.

Flujo:

```text
DISCOVER PROJECTS
      ↓
BUILD DEPENDENCY GRAPH
      ↓
CALCULATE ANALYSIS WAVES
      ↓
ANALYSIS WAVE 1
      ↓
STORE RESULTS
      ↓
PROPAGATE CONTEXT
      ↓
ANALYSIS WAVE 2
      ↓
...
      ↓
WORKSPACE ANALYSIS REPORT
```

## 5. Paralelismo en análisis

Los proyectos de la misma wave pueden analizarse en paralelo.

```python
for wave in analysis_waves:
    with ThreadPoolExecutor(
        max_workers=config.max_parallel_analysis
    ) as executor:
        futures = {
            executor.submit(analyze_project, project): project
            for project in wave.projects
        }

        for future in as_completed(futures):
            result = future.result()
            register_analysis_result(result)

    propagate_analysis_context(wave)
```

## 6. Contexto heredado

Un proyecto dependiente puede consumir hallazgos de sus dependencias.

```json
{
  "project": "common-epay",
  "dependencyContext": [
    {
      "project": "common-datasource",
      "findings": [
        "USES_JNDI",
        "JAVA8",
        "SPRING_XML"
      ]
    }
  ]
}
```

Esto mejora recomendaciones y evita analizar cada módulo como si fuera aislado.

## 7. Fallos durante análisis

El análisis debe ser tolerante.

Ejemplo:

```text
Wave A1
common-datasource = FAILED
common-activemq = SUCCEEDED
```

En Wave A2:

```text
common-catalogue
depends on common-activemq
→ ANALYZE

common-epay
depends on common-datasource
→ ANALYZE_WITH_PARTIAL_CONTEXT
```

Estados:

```text
ANALYSIS_SUCCEEDED
ANALYSIS_PARTIAL
ANALYSIS_FAILED
ANALYSIS_BLOCKED
```

Un fallo de análisis no debe bloquear automáticamente el análisis local de un dependiente si todavía puede revisarse su código.

## 8. Estado de Analysis Wave

Agregar:

```text
ANALYSIS_WAVE_PENDING
ANALYSIS_WAVE_RUNNING
ANALYSIS_WAVE_SUCCEEDED
ANALYSIS_WAVE_PARTIAL_SUCCESS
ANALYSIS_WAVE_FAILED
```

Reglas:

```text
todos PASS
→ ANALYSIS_WAVE_SUCCEEDED

mezcla PASS/PARTIAL/FAIL
→ ANALYSIS_WAVE_PARTIAL_SUCCESS

todos FAIL
→ ANALYSIS_WAVE_FAILED
```

Generar:

```text
analysis-waves.json
workspace-analysis.md
```

## 9. MigrationWaveOrchestrator

Crear:

```text
orchestration/
└── migration_wave_orchestrator.py
```

Responsabilidades:

- calcular migration waves;
- revisar estados de dependencias;
- separar `RUNNABLE` y `BLOCKED`;
- migrar proyectos elegibles;
- compilar y probar cada candidate;
- continuar con proyectos independientes;
- propagar bloqueos solo por dependencia real.

## 10. Error a evitar

Incorrecto:

```python
for wave in waves:
    results = migrate_wave(wave)

    if any(result.failed for result in results):
        break
```

Eso cancela todo el workspace.

Correcto:

```python
for wave in waves:
    runnable = []
    blocked = []

    for project in wave.projects:
        failed_dependencies = get_failed_dependencies(project)

        if failed_dependencies:
            blocked.append({
                "project": project,
                "reason": "BLOCKED_BY_DEPENDENCY",
                "dependencies": failed_dependencies
            })
        else:
            runnable.append(project)

    register_blocked(blocked)

    results = migrate_projects_parallel(runnable)

    register_results(results)
    register_wave_status(
        calculate_wave_status(results, blocked)
    )
```

## 11. Ejemplo de migración parcial

```text
Wave M1
common-datasource = FAILED
common-activemq = SUCCEEDED
```

Wave M2:

```text
common-catalogue
depends on common-activemq
→ RUN

common-epay
depends on common-datasource
→ BLOCKED_BY_DEPENDENCY
```

Resultado:

```text
common-catalogue = SUCCEEDED
common-epay = BLOCKED
```

La Wave M2 no debe cancelarse completa.

## 12. Estados de Migration Wave

Agregar:

```text
WAVE_PENDING
WAVE_RUNNING
WAVE_SUCCEEDED
WAVE_PARTIAL_SUCCESS
WAVE_FAILED
WAVE_BLOCKED
```

Reglas:

```text
todos los runnable PASS
→ WAVE_SUCCEEDED

algunos PASS y otros FAIL/BLOCKED
→ WAVE_PARTIAL_SUCCESS

todos los runnable FAIL
→ WAVE_FAILED

ningún proyecto runnable
→ WAVE_BLOCKED
```

## 13. Maven debe ejecutarse por ProjectContext

Incorrecto:

```bash
cd attempt-1/candidate
mvn clean verify
```

Correcto:

```bash
mvn -f projects/common-activemq/candidate/pom.xml clean verify
mvn -f projects/common-datasource/candidate/pom.xml clean verify
```

Cada módulo debe usar:

```text
ProjectContext.candidatePom
```

## 14. Maven Execution Guard

Antes de ejecutar Maven:

```python
if not project.candidate_pom.exists():
    return BuildResult(
        status="BLOCKED",
        reason="MISSING_CANDIDATE_POM"
    )
```

Validar:

```text
candidate dir exists
pom.xml exists
pom.xml readable
artifactId matches ProjectContext
```

Si aparece `MissingProjectException`, clasificar como:

```text
CANDIDATE_BUILD_CONFIGURATION_ERROR
reason = MAVEN_PROJECT_NOT_FOUND
```

No como error de compilación del código.

## 15. Logging

Formato recomendado:

```text
[workspace][analysis-wave-1][common-datasource][analysis] START
[workspace][analysis-wave-1][common-datasource][analysis] PASS

[workspace][migration-wave-1][common-activemq][build] PASS
[workspace][migration-wave-2][common-epay][migration] BLOCKED_BY_DEPENDENCY
```

Campos:

```text
workspaceId
waveType
waveId
projectId
attemptId
phase
```

## 16. Estructura de salida

```text
attempt-1/
├── projects/
│   ├── common-activemq/
│   │   ├── analysis/
│   │   ├── baseline/
│   │   ├── candidate/
│   │   ├── reports/
│   │   └── logs/
│   ├── common-catalogue/
│   ├── common-datasource/
│   ├── common-epay/
│   └── common-rate-exchange/
└── workspace/
    ├── analysis-waves.json
    ├── migration-waves.json
    ├── dependency-graph.json
    ├── workspace-analysis.md
    └── workspace-migration.md
```

## 17. UI recomendada

Separar visualmente análisis y migración:

```text
ANÁLISIS

Wave 1
✓ common-activemq
⚠ common-datasource

Wave 2
✓ common-catalogue
⚠ common-epay
```

Y:

```text
MIGRACIÓN

Wave 1
✓ common-activemq
✗ common-datasource

Wave 2
✓ common-catalogue
⊘ common-epay
  Blocked by common-datasource
```

## 18. Estados globales

Análisis:

```text
ANALYSIS_SUCCEEDED
ANALYSIS_PARTIAL_SUCCESS
ANALYSIS_FAILED
```

Migración:

```text
MIGRATION_SUCCEEDED
MIGRATION_PARTIAL_SUCCESS
MIGRATION_FAILED
MIGRATION_BLOCKED
```

Workspace:

```text
SUCCEEDED
PARTIAL_SUCCESS
FAILED
BLOCKED
```

## 19. No marcar BLOCKED como FAILED

Ejemplo correcto:

```json
{
  "project": "common-epay",
  "status": "BLOCKED",
  "reason": "BLOCKED_BY_DEPENDENCY",
  "blockedBy": ["common-datasource"]
}
```

El proyecto no debe figurar como `FAILED` si nunca se intentó migrar.

## 20. Reintentos

Permitir retry individual.

Ejemplo:

```text
retry common-datasource
```

Si luego pasa:

```text
common-datasource = SUCCEEDED
```

recalcular:

```text
common-epay
BLOCKED → READY
```

No repetir proyectos ya exitosos.

## 21. Persistencia de estado

Guardar:

```json
{
  "common-datasource": {
    "analysis": "SUCCEEDED",
    "migration": "FAILED"
  },
  "common-epay": {
    "analysis": "PARTIAL",
    "migration": "BLOCKED"
  }
}
```

Esto habilita `resume` y `retry`.

## 22. Criterios de aceptación — análisis

### A1

Workspace con 5 proyectos:

```text
projectsDiscovered = 5
projectsAnalyzed = 5
```

PASS.

### A2

Un proyecto de Wave A1 falla y los independientes continúan.

PASS.

### A3

Un dependiente puede quedar:

```text
ANALYSIS_PARTIAL
```

si recibe contexto incompleto.

PASS.

### A4

Se generan:

```text
analysis-waves.json
workspace-analysis.md
```

PASS.

## 23. Criterios de aceptación — migración

### M1

Un módulo falla y solo sus dependientes quedan bloqueados.

PASS.

### M2

Módulos independientes de waves siguientes continúan.

PASS.

### M3

Cada build usa:

```text
ProjectContext.candidatePom
```

PASS.

### M4

Si falta candidate POM:

```text
MISSING_CANDIDATE_POM
```

antes de Maven.

PASS.

### M5

Si hay éxitos y fallos/bloqueos:

```text
workspaceStatus = PARTIAL_SUCCESS
```

PASS.

## 24. Arquitectura final

```text
WorkspaceDiscovery
        ↓
DependencyGraphBuilder
        ↓
WavePlanner
        ↓
AnalysisWaveOrchestrator
        ↓
WorkspaceAnalysisAggregator
        ↓
MigrationPlanner
        ↓
MigrationWaveOrchestrator
        ↓
WorkspaceMigrationAggregator
        ↓
FinalReport
```

## 25. Prioridad

```text
CRITICAL
```

El soporte multiproyecto solo está completo cuando el sistema coordina correctamente tanto el análisis como la migración de todos los módulos, respetando dependencias, propagando contexto y tolerando fallos parciales.

# Prompt maestro para Claude

Extiende el proyecto existente `Migration Factory` para soportar:

```text
IBM DataStage → Apache Airflow
```

NO crees otra plataforma.

Reutiliza:
- workspace
- source ingestion
- job orchestration
- dependency graph
- waves
- retry
- state machine
- AI assistance
- reporting
- packaging
- UI/API base

## 1. Migration Type
Agregar:
```python
JAVA_CAMEL
DATASTAGE_AIRFLOW
```

mediante adapter abstraction.

## 2. Nuevo paquete
Crear:
```text
backend/mf/datastage/
```

con parser, model, analysis, registry, planning, generation, validation, testing y reporting.

## 3. DSX Parser
Debe soportar múltiples jobs por archivo y extraer:
- jobs
- sequences
- stages
- links
- parameters
- properties
- connections
- JobActivity
- error handling

Persistir:
```text
datastage-ir.json
```

## 4. IR
Crear:
- DataStageProject
- DataStageJob
- DataStageSequence
- DataStageStage
- DataStageLink
- DataStageParameter
- SourceEvidence

Los módulos posteriores no parsean DSX directamente.

## 5. Dependency graph
Derivar dependencias desde sequences, files, staging, producer/consumer y relaciones explícitas.
Generar waves topológicas.
Permitir paralelismo.

## 6. Complejidad
Clasificar:
```text
LOW
MEDIUM
HIGH
VERY_HIGH
```

Migrabilidad:
```text
AUTO_MIGRATABLE
SEMIAUTOMATIC
MANUAL_REVIEW
ARCHITECTURAL_REDESIGN
```

## 7. Registry
Crear `DataStageAirflowRegistry`.

Mappings:
```text
CJobActivity → task dependency
CCondition → branch/short circuit
CExecCommandActivity → BashOperator
Terminator → failure task
ExceptionHandler → trigger rules/callbacks
OracleConnectorPX → OracleHook / SQLExecuteQueryOperator
PxSequentialFile → file handling
PxDataSet → staging abstraction
Filter → SQL WHERE
Join → SQL JOIN
Sort → SQL ORDER BY
Merge → SQL MERGE
RemoveDuplicates → DISTINCT/ROW_NUMBER
Transformer → semantic SQL/Python/manual decision
Copy → fan-out
```

No hacer mappings ciegos.

## 8. Generation
Generar:
```text
dags/
sql/
src/
config/
tests/
migration-report/
```

Airflow orquesta; no recrear ETL visual dentro del DAG.
Preferir SQL pushdown para Oracle.

## 9. Transformer
Estados:
```text
TRANSLATED_SQL
TRANSLATED_PYTHON
PARTIAL
MANUAL_REVIEW
```

IA solo como fallback asistido.
Todo output IA pasa validadores determinísticos.

## 10. Parameters/connections
No hardcodear secretos.
Usar Airflow Connections/Variables/params/secret backend.
Generar manifest sin credenciales.

## 11. Validation
Agregar:
```text
DSX_PARSE_GATE
DEPENDENCY_GRAPH_GATE
STAGE_MAPPING_GATE
SECRET_GATE
DAG_PARSE_GATE
SQL_VALIDATION_GATE
RECONCILIATION_GATE
PACKAGE_GATE
```

Usar DagBag.

## 12. Reconciliation
Generar checks:
- row count
- null count
- duplicate count
- business key count
- sum
- min/max
- checksum
- sample rows

No afirmar functional equivalence solo porque el DAG importa.

## 13. Estados
Integrar con state model existente:
```text
AUTO_MIGRATED
SEMIAUTOMATIC
MANUAL_REVIEW
BLOCKED
FAILED
```

Overall:
```text
SUCCEEDED
PARTIAL_SUCCESS
BLOCKED
FAILED
```

## 14. Seguridad
DSX = input no confiable.
No ejecutar comandos/SQL/routines durante analysis.
Redactar secretos antes de IA.

## 15. Tests
Agregar unit/golden/integration tests con fixtures representativos.

## 16. BIBES
Usar los DSX reales como corpus de regresión.

Meta inicial:
```text
100% jobs parsed
100% jobs inventoried
100% jobs classified
dependency graph generated
manual actions explicit
```

Luego migrar piloto:
```text
1 LOW
1 MEDIUM
1 HIGH
1 master sequence
```

Restricciones:
- no romper Java/Camel;
- no mezclar registries;
- no introducir secrets;
- no marcar SUCCESS sin gates;
- preservar trazabilidad source → IR → generated artifact.

Lee y aplica los MD 51–67 de este paquete como contrato técnico.

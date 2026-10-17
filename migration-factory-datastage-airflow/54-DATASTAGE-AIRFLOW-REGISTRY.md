# 54 — Registry DataStage → Airflow

Estados:

```text
SUPPORTED
SEMANTIC_MAPPING
CONDITIONAL
REQUIRES_SQL_REWRITE
REQUIRES_PYTHON_REWRITE
ARCHITECTURAL_REDESIGN
MANUAL_REVIEW
UNSUPPORTED
```

Mappings iniciales:

| DataStage | Target |
|---|---|
| CJobActivity | task dependency |
| CCondition | branching / short circuit |
| CExecCommandActivity | BashOperator |
| TerminatorActivity | fail task / AirflowException |
| ExceptionHandler | callbacks / trigger rules |
| OracleConnectorPX | OracleHook / SQLExecuteQueryOperator |
| PxSequentialFile | file handling |
| PxDataSet | staging abstraction |
| Transformer | SQL/Python/manual |
| Filter | SQL WHERE |
| Join | SQL JOIN |
| Merge | SQL MERGE |
| Sort | SQL ORDER BY / engine |
| RemoveDuplicates | DISTINCT / ROW_NUMBER |
| Copy | fan-out |
| ColumnGenerator | projection |
| RowGenerator | SQL/Python generation |

No hacer mappings ciegos.

Ejemplo correcto:

```python
StageDecision(
    stage_type="Transformer",
    target="SQL",
    status="SEMANTIC_MAPPING",
    confidence="HIGH",
    reason="expressions translatable to SQL"
)
```

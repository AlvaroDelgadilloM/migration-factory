# 60 — Validación y reconciliación

No declarar éxito porque el DAG importe.

Gates:
```text
IR_VALIDATION
MAPPING_VALIDATION
DAG_SYNTAX_VALIDATION
AIRFLOW_IMPORT_VALIDATION
SQL_VALIDATION
DEPENDENCY_VALIDATION
RECONCILIATION_TESTS
```

Comparar DataStage vs target:
- row count
- null count
- duplicate count
- business key count
- sum
- min/max
- checksum
- sample rows

Estados:
```text
TECHNICALLY_VALID
FUNCTIONALLY_UNVERIFIED
FUNCTIONALLY_VALIDATED
```

Generar:
```text
tests/reconciliation/<job>.sql
```

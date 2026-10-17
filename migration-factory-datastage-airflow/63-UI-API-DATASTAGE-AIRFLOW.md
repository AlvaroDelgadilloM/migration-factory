# 63 — UI/API

Nueva opción:
```text
Migration Type
○ Java/Camel
● DataStage → Airflow
```

Formulario:
- nombre
- DSX/ZIP
- Airflow target
- execution engine preference
- DB target
- environment
- connection policy

Dashboard:
```text
total jobs
flows
AUTO_MIGRATABLE
SEMIAUTOMATIC
MANUAL_REVIEW
BLOCKED
```

Vista por job:
- original
- complexity
- stages
- dependencies
- mappings
- generated DAG
- generated SQL/Python
- validation
- manual actions

API:
```json
{"migrationType":"DATASTAGE_AIRFLOW"}
```

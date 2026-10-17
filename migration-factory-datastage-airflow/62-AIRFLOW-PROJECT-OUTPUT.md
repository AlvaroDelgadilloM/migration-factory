# 62 — Output del proyecto Airflow

```text
airflow-bibes/
├── dags/
├── sql/
├── src/
├── config/
├── tests/
│   ├── dags/
│   └── reconciliation/
├── migration-report/
├── requirements.txt
├── pyproject.toml
├── docker-compose.yml
└── README.md
```

Agregar:
```text
.migration/manifest.json
```

con:
- source type
- DataStage version detectada
- target Airflow
- job count
- auto migrated
- manual review
- failed
- hashes
- timestamp

# 57 — Airflow DAG Generator

Principios:
- Airflow orquesta.
- SQL en `.sql`.
- Python reusable fuera del DAG.
- secretos en Connections/secret backend.
- nombres trazables al original.

Sequence:
```text
Job A → Job B → branch → Job C / fail
```

Generar:
```text
dags/<flow>.py
```

Usar TaskGroup si aplica.

Cada DAG debe documentar:
- DataStage source
- original job/sequence
- confidence
- manual actions

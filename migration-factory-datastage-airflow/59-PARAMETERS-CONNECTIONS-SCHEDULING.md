# 59 — Parámetros, conexiones y scheduling

Mapeo:
```text
runtime parameter → DAG params
environment value → Airflow Variable/config
credential → Airflow Connection/secret backend
```

Nunca hardcodear secretos.

Generar:
```text
connection-manifest.json
```

sin passwords.

Traducir schedules:
- cron
- daily
- hourly
- weekly
- manual
- event-driven

Timezone explícito.

Separar DEV/QA/PROD de la lógica del DAG.

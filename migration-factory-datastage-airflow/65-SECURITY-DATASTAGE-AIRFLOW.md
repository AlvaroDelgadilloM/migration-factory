# 65 — Seguridad

DSX es input no confiable.

No ejecutar durante analysis:
- shell commands
- SQL contra PROD
- scripts externos
- routines

Secret scan sobre:
- DSX
- config generado
- DAGs
- SQL
- reports

Si hay credenciales:
```text
SECRET_FINDING
```

Redactar antes de enviar contexto a IA.

Usar:
- Airflow Connections
- Secret Backend
- environment references

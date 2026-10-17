# 51 — Arquitectura DataStage → Airflow

## Principio
No crear una herramienta separada.

```text
Migration Factory
├── Common Engine
├── Java/Camel Adapter
└── DataStage/Airflow Adapter
```

Reutilizar:
- workspace
- source ingestion
- dependency graph
- waves
- state machine
- retry
- AI assistance
- reporting
- packaging
- UI/API base

Agregar:

```python
class MigrationType(Enum):
    JAVA_CAMEL = "JAVA_CAMEL"
    DATASTAGE_AIRFLOW = "DATASTAGE_AIRFLOW"
```

Pipeline:

```text
DSX/ZIP
↓
Discovery
↓
DSX Parser
↓
IR
↓
Dependency Analyzer
↓
Complexity Analyzer
↓
Migration Planner
↓
Stage Registry
↓
DAG/SQL/Python Generation
↓
Validation
↓
Reconciliation
↓
Report
↓
Airflow ZIP
```

Estructura sugerida:

```text
backend/mf/datastage/
├── parser/
├── model/
├── analysis/
├── registry/
├── planning/
├── generation/
├── validation/
├── testing/
├── reporting/
└── adapter.py
```

No mezclar lógica DataStage en `camel_compat.py`.

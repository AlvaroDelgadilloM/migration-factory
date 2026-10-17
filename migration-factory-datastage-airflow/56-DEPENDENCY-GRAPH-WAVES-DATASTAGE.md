# 56 — Dependency Graph y Waves

Fuentes de dependencia:
- Sequence → JobActivity
- chaining
- producer/consumer
- temp datasets
- files
- staging tables
- predecessor/successor
- schedules

Construir:
```text
Project → Dependency Graph → Topological Levels → Waves
```

Jobs independientes deben migrarse en paralelo.

Ejemplo:
```text
A ─→ D
B ─→ D
C

Wave 0: A,B,C
Wave 1: D
```

Estados:
```text
MIGRATED
PARTIAL
BLOCKED_BY_DEPENDENCY
FAILED
MANUAL_REVIEW
```

Si una dependencia se recupera, recalcular bloqueos.

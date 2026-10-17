# 64 — Test Strategy

Unit:
- DSX parser
- registry
- expression translation
- complexity score
- graph
- naming
- DAG generation

Golden tests:
```text
fixture.dsx
fixture.ir.json
fixture_dag.py
fixture.sql
```

DAG tests:
```python
DagBag(...)
```

Validar:
- import_errors == 0
- DAG IDs
- tasks
- dependencies

Integration:
- Oracle test DB
- file fixtures
- branching
- retry

Reconciliation:
legacy expected vs new output.

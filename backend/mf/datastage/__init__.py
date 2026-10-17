"""IBM DataStage -> Apache Airflow adapter (docs: migration-factory-datastage-airflow/51-68).

DSX -> parser -> IR (model) -> analysis (inventory, dependency graph, waves, complexity) -> registry decisions ->
generation (DAG/SQL/config/tests) -> validation gates -> Airflow project ZIP. The DSX is untrusted input: nothing in it
(shell commands, SQL, routines) is executed. Stdlib only, like the Camel scanner: runs in the CLI and in the worker.
"""

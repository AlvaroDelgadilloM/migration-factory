"""Airflow project generation (docs 57-59, 62). Airflow orchestrates: SQL lives in sql/, reusable Python in src/, secrets
in Connections (only their ids appear), environment values in Variables, runtime values in DAG params. Every DAG,
SQL file and task names the DataStage job/stage it comes from."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .registry import param_resolver  # noqa: F401  (kept for parity with the registry's parameter policy)
from .sqlgen import Composer, Jinja, SECRET_MEMBER, ddl, ident

AIRFLOW = {'airflow': '2.10.5', 'oracle': '4.0.0', 'common-sql': '1.21.0', 'oracledb': '2.5.1', 'python': '3.12'}
_ACT_TOKEN = re.compile(r'#([A-Za-z_]\w*)\.(\$?\w+)#')


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def connection_id(job, default='oracle_default') -> str:
    """One Airflow Connection per DataStage parameter set that carries the Oracle credentials; a job without its own
    Oracle stage (files and DataSets only) uses the project's connection, where its staging tables live."""
    for s in job.stages:
        server = s.properties.get('Connection/Server') or ''
        m = re.match(r'#(\w+)\.', server)
        if m:
            return f'oracle_{ident(m[1])}'
    return default


def connections(project) -> list[dict]:
    out = {}
    for j in project.jobs:
        for s in j.stages:
            if s.stage_type != 'OracleConnectorPX':
                continue
            cid = connection_id(j)
            entry = out.setdefault(cid, {'connId': cid, 'connType': 'oracle', 'jobs': set(), 'fields': {}, 'literalCredentials': []})
            entry['jobs'].add(j.name)
            for f, k in (('host', 'Connection/Server'), ('login', 'Connection/Username'), ('password', 'Connection/Password')):
                v = s.properties.get(k)
                if v is None:
                    continue
                if '#' in v:
                    entry['fields'][f] = {'from': v}  # a DataStage parameter reference, never a value
                elif f == 'password':
                    entry['literalCredentials'].append(f'{j.name}/{s.name}')  # SECRET_FINDING: redacted by the parser, never copied
                else:
                    entry['fields'][f] = {'value': v}
    return [e | {'jobs': sorted(e['jobs'])} for e in out.values()]


class Project:
    def __init__(self, project, analysis, out: Path, name: str, timezone='America/Mexico_City'):
        self.p, self.a, self.out, self.name, self.tz = project, analysis, out, name, timezone
        self.pkg = ident(name) + '_migrated'
        self.dec = analysis['decisions']
        self.jobs_steps, self.variables, self.params, self.staging, self.results, self.dags = {}, set(), {}, {}, {}, []
        ids = [connection_id(j, None) for j in project.jobs]
        ids = [i for i in ids if i]
        self.default_conn = max(set(ids), key=ids.count) if ids else 'oracle_default'

    # --- parallel jobs ---------------------------------------------------------------------------------------------
    def job_steps(self, job):
        if job.name not in self.jobs_steps:
            c = Composer(job)
            steps = c.steps()
            self.staging.update(c.staging)
            self.variables |= c.jinja.variables
            for i, st in enumerate(steps):
                rel = f'sql/{ident(job.name)}/{i:02d}_{st.name}.sql'
                head = (f'-- Generado por Migration Factory desde DataStage\n-- job: {job.name} ({job.source_file})\n-- etapa: {st.stage}\n'
                        f'-- estado: {st.status}\n' + ''.join(f'-- revisar: {x}\n' for x in st.issues))
                if st.kind in ('sql', 'file_export'):
                    write(self.out / rel, head + st.sql)
                    st.sql_file = rel
            self.jobs_steps[job.name] = steps
            statuses = {s.status for s in steps}
            mig = self.a_job(job.name)['migratability']
            state = ('MANUAL_REVIEW' if 'MANUAL' in statuses or mig in ('MANUAL_REVIEW', 'ARCHITECTURAL_REDESIGN')
                     else 'SEMIAUTOMATIC' if 'REVIEW' in statuses or mig != 'AUTO_MIGRATABLE' else 'AUTO_MIGRATED')
            self.results[job.name] = {'state': state, 'steps': [{'task': s.name, 'kind': s.kind, 'stage': s.stage, 'status': s.status,
                                                                  'issues': s.issues, 'sqlFile': getattr(s, 'sql_file', None)} for s in steps]}
        return self.jobs_steps[job.name]

    def a_job(self, name):
        return next(x for x in self.a['jobs'] if x['job'] == name)

    def job_tasks(self, job, indent='        ') -> list[str]:
        """Lines of a TaskGroup body for a parallel job; returns code that defines `first`/`last` in the group."""
        steps = self.job_steps(job)
        conn = connection_id(job, self.default_conn)
        lines = []
        for st in steps:
            tid = st.name
            if st.status == 'MANUAL':
                reason = '; '.join(st.issues)[:900]
                lines.append(f'{indent}{tid} = PythonOperator(task_id={tid!r}, python_callable=runtime.manual_step, '
                             f'op_kwargs={{"job": {job.name!r}, "stage": {st.stage!r}, "reason": {reason!r}}})')
            elif st.kind == 'sql':
                lines.append(f'{indent}{tid} = SQLExecuteQueryOperator(task_id={tid!r}, conn_id={conn!r}, sql={st.sql_file!r}, '
                             f'split_statements=True)')
            elif st.kind == 'file_load':
                lines.append(f'{indent}{tid} = PythonOperator(task_id={tid!r}, python_callable=runtime.load_file, op_kwargs={{"conn_id": {conn!r}, '
                             f'"stage": {job.name + "/" + st.stage!r}, "path": {st.resource!r}, "table": {st.target!r}, "columns": {st.columns!r}}})')
            elif st.kind == 'file_export':
                lines.append(f'{indent}{tid} = PythonOperator(task_id={tid!r}, python_callable=runtime.export_file, op_kwargs={{"conn_id": {conn!r}, '
                             f'"stage": {job.name + "/" + st.stage!r}, "path": {st.resource!r}, "sql_file": {st.sql_file!r}}})')
            else:
                lines.append(f'{indent}{tid} = EmptyOperator(task_id={tid!r})  # {"; ".join(st.issues)}')
        if not steps:
            lines.append(f'{indent}EmptyOperator(task_id="sin_pasos")  # el job no escribe datos')
        loads = [s.name for s in steps if s.kind == 'file_load']
        others = [s.name for s in steps if s.kind != 'file_load']
        if loads and others:  # files are loaded into staging before any SQL reads them
            lines.append(f'{indent}[{", ".join(loads)}] >> EmptyOperator(task_id="cargas_listas") >> [{", ".join(others)}]')
        return lines

    # --- DAG files ---------------------------------------------------------------------------------------------------
    def header(self, name, kind, jobs, source):
        a = self.a_job(name)
        manual = [m for j in jobs for m in self.a_job(j)['manualActions']]
        manual += [f'{j}/{s["task"]}: {"; ".join(s["issues"])}' for j in jobs if j in self.results
                   for s in self.results[j]['steps'] if s['status'] == 'MANUAL']
        doc = (f'# {name}\n\nGenerado por Migration Factory desde IBM DataStage.\n\n- Origen: `{source}`\n- {kind} original: `{name}`\n'
               f"- Complejidad: {a['complexity']} · migrabilidad: {a['migratability']} · confianza: {a['confidence']}\n"
               f'- Programación: no viene en el DSX (la ejecutaba un planificador externo): `schedule=None`, definir en config/schedules.yaml\n')
        if manual:
            doc += '\n## Acciones manuales\n\n' + ''.join(f'- {m}\n' for m in sorted(set(manual))[:60])
        return doc

    def dag_head(self, dag_id, doc, params):
        return f'''"""{doc.replace('"""', "'''")}"""
from __future__ import annotations

import sys
from pathlib import Path

import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import BranchPythonOperator, PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.utils.task_group import TaskGroup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from {self.pkg} import runtime  # noqa: E402

with DAG(
    dag_id={dag_id!r},
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz={self.tz!r}),
    catchup=False,
    max_active_runs=1,
    template_searchpath=[str(ROOT)],
    params={params!r},
    default_args={{"retries": 0, "owner": "migration-factory"}},
    tags=["datastage", {ident(self.name)!r}],
    doc_md=__doc__,
) as dag:
'''

    def plain_params(self, job):
        return {p.name: p.default if p.default not in (None, '(As pre-defined)') else None for p in job.parameters if not p.is_set}

    def sequence_dag(self, seq):
        acts = {s.name: s for s in seq.stages}
        tids = {n: ident(n) for n in acts}
        jinja = Jinja(seq, {n: tids[n] for n in acts})
        params = self.plain_params(seq)
        called = []
        body, after = [], []
        # A TerminatorActivity reached from several places (a condition's "otherwise" and the exception handler) runs in
        # DataStage only when one of them fires. One Airflow task per entry keeps that: with a single task, the branch could
        # not skip it (it is reachable from the chosen branch through the handler) and it would abort a successful run.
        inbound = {n: [l.source_stage for l in seq.links if l.target_stage == n and l.source_stage in tids] for n in acts}
        split = {n for n in acts if acts[n].stage_type == 'CTerminatorActivity' and len(inbound[n]) > 1}

        def target(l):
            return f'{tids[l.target_stage]}__{tids[l.source_stage]}' if l.target_stage in split else tids[l.target_stage]
        for s in seq.stages:
            tid, t, p = tids[s.name], s.stage_type, s.properties
            if t == 'CJobActivity':
                callee = self.p.job(p.get('Jobname') or '')
                if callee is None:
                    body.append(f'    {tid} = PythonOperator(task_id={tid!r}, python_callable=runtime.manual_step, op_kwargs={{"job": {seq.name!r}, '
                                f'"stage": {s.name!r}, "reason": "el job {p.get("Jobname")} no está en las exportaciones"}})')
                elif callee.job_type == 'SEQUENCE':
                    body.append(f'    {tid} = TriggerDagRunOperator(task_id={tid!r}, trigger_dag_id={ident(callee.name)!r}, wait_for_completion=True)')
                else:
                    called.append(callee.name)
                    params |= self.plain_params(callee)
                    body.append(f'    with TaskGroup(group_id={tid!r}, tooltip={callee.name!r}) as {tid}:')
                    body += self.job_tasks(callee)
            elif t == 'CUserVarsActivity':
                body.append(f'    {tid} = PythonOperator(task_id={tid!r}, python_callable=runtime.user_variables, op_kwargs={{"activity": {s.name!r}, '
                            f'"variables": {dict(p.get("variables") or {})!r}}})')
            elif t == 'CCondition':
                outs = [l for l in seq.links if l.source_stage == s.name]
                branches = [{'task': target(l), 'type': (l.trigger or {}).get('type'), 'expression': (l.trigger or {}).get('expression')}
                            for l in outs if l.target_stage in tids]
                body.append(f'    {tid} = BranchPythonOperator(task_id={tid!r}, python_callable=runtime.branch, op_kwargs={{"branches": {branches!r}, '
                            f'"activities": {tids!r}}})')
            elif t == 'CTerminatorActivity':
                for task in ([f'{tid}__{tids[src]}' for src in inbound[s.name]] if s.name in split else [tid]):
                    body.append(f'    {task} = PythonOperator(task_id={task!r}, python_callable=runtime.terminate, '
                                f'op_kwargs={{"message": {p.get("FinalMessage") or ""!r}}})')
            elif t == 'CExceptionHandler':
                body.append(f'    {tid} = EmptyOperator(task_id={tid!r}, trigger_rule="one_failed")')
                after.append(tid)
            elif t == 'CExecCommandActivity':
                cmd = jinja(p.get('Path') or '') + ((' ' + jinja(p['Parameters'])) if p.get('Parameters') else '')
                body.append(f'    {tid} = BashOperator(task_id={tid!r}, bash_command={cmd!r})  # no se ejecutó durante el análisis')
            elif t == 'CSequencer':
                rule = 'all_success' if p.get('SequencerType') == '0' else 'one_success'
                body.append(f'    {tid} = EmptyOperator(task_id={tid!r}, trigger_rule={rule!r})')
            else:
                body.append(f'    {tid} = PythonOperator(task_id={tid!r}, python_callable=runtime.manual_step, op_kwargs={{"job": {seq.name!r}, '
                            f'"stage": {s.name!r}, "reason": "actividad {t} sin mapeo"}})')
        self.variables |= jinja.variables
        deps = []
        for l in seq.links:
            if l.source_stage in tids and l.target_stage in tids:
                deps.append(f'    {tids[l.source_stage]} >> {target(l)}')
        for h in after:  # the exception handler fires when any job activity fails
            jobs = [tids[s.name] for s in seq.stages if s.stage_type == 'CJobActivity']
            if jobs:
                deps.append(f'    [{", ".join(jobs)}] >> {h}')
        multi = {l.target_stage for l in seq.links if sum(1 for x in seq.links if x.target_stage == l.target_stage) > 1}
        for n in sorted(multi):
            if acts[n].stage_type not in ('CSequencer', 'CTerminatorActivity', 'CExceptionHandler'):
                deps.append(f'    {tids[n]}.trigger_rule = "none_failed_min_one_success"  # varias entradas en DataStage: revisar')
        doc = self.header(seq.name, 'Secuencia', called, seq.source_file)
        code = self.dag_head(ident(seq.name), doc, params) + '\n'.join(body) + '\n\n' + '\n'.join(deps) + '\n'
        rel = f'dags/{ident(seq.name)}.py'
        write(self.out / rel, code)
        self.dags.append({'dagId': ident(seq.name), 'file': rel, 'source': seq.name,
                          'tasks': None, 'edges': len(deps), 'jobs': called})
        states = [self.results[c]['state'] for c in called]
        if any(acts[n].stage_type == 'CJobActivity' and self.p.job(acts[n].properties.get('Jobname') or '') is None for n in acts):
            state = 'BLOCKED'
        elif any(d['status'] in ('MANUAL_REVIEW', 'UNSUPPORTED') for d in self.dec[seq.name]) or 'MANUAL_REVIEW' in states:
            state = 'MANUAL_REVIEW'
        elif any(d['status'] != 'SUPPORTED' for d in self.dec[seq.name]) or 'SEMIAUTOMATIC' in states:
            state = 'SEMIAUTOMATIC'
        else:
            state = 'AUTO_MIGRATED'
        self.results[seq.name] = {'state': state, 'dag': rel, 'jobs': called}

    def job_dag(self, job):
        body = [f'    with TaskGroup(group_id={ident(job.name)!r}, tooltip={job.name!r}) as {ident(job.name)}:'] + self.job_tasks(job)
        doc = self.header(job.name, 'Job paralelo', [job.name], job.source_file)
        code = self.dag_head(ident(job.name), doc, self.plain_params(job)) + '\n'.join(body) + '\n'
        rel = f'dags/{ident(job.name)}.py'
        write(self.out / rel, code)
        self.dags.append({'dagId': ident(job.name), 'file': rel, 'source': job.name, 'tasks': None, 'edges': 0, 'jobs': [job.name]})
        self.results[job.name]['dag'] = rel

    # --- whole project -----------------------------------------------------------------------------------------------
    def generate(self) -> dict:
        called = {c for x in self.a['jobs'] for c in x['calls']}
        for j in self.p.jobs:
            if j.job_type == 'PARALLEL':
                self.job_steps(j)
        for s in self.p.sequences:
            self.sequence_dag(s)
        for j in self.p.jobs:
            if j.job_type == 'PARALLEL' and j.name not in called:
                self.job_dag(j)
        for j in self.p.jobs:
            if j.job_type == 'PARALLEL' and j.name in called:
                self.results[j.name].setdefault('dag', next((d['file'] for d in self.dags if j.name in d['jobs']), None))
        self.support_files()
        return self.results

    def support_files(self):
        o = self.out
        write(o / f'src/{self.pkg}/__init__.py', '')
        write(o / f'src/{self.pkg}/runtime.py', RUNTIME)
        write(o / 'sql/ddl/staging.sql', '-- Tablas de staging que reemplazan a los DataSets (.ds) y archivos de DataStage.\n'
              '-- Revisar tipos y longitudes antes de crearlas: el DSX no trae precisión para todas las columnas.\n\n'
              + '\n'.join(ddl(t, c + ([{'name': 'MF_SEQ', 'sqlType': '3', 'nullable': False}] if 'STG_FILE_' in t else []))
                          for t, c in sorted(self.staging.items())))
        conns = connections(self.p)
        write(o / 'config/connection-manifest.json', json.dumps({
            'policy': 'Credenciales solo en Airflow Connections o en el secret backend; este archivo no contiene valores secretos.',
            'connections': [{k: v for k, v in c.items() if k != 'literalCredentials'} for c in conns]}, ensure_ascii=False, indent=2))
        envs = {'dev': {}, 'qa': {}, 'prod': {}}
        for v in sorted(self.variables):
            for e in envs:
                envs[e][v] = None
        write(o / 'config/variables.json', json.dumps({'note': 'Valores por ambiente de los parameter sets de DataStage (no vienen en el DSX). '
                                                       'Cargar con `airflow variables import`.', 'environments': envs}, ensure_ascii=False, indent=2))
        write(o / 'config/files.yaml', '# Formato de cada archivo plano (DataStage PxSequentialFile). Sin esta entrada la tarea falla.\n'
              '# <job>/<etapa>:\n#   delimiter: "|"\n#   quotechar: null\n#   header: false\n#   encoding: "utf-8"\n')
        write(o / 'config/schedules.yaml', '# El DSX no incluye la programación (la ejecutaba un planificador externo).\n'
              '# <dag_id>: "0 2 * * *"   # zona horaria: ' + self.tz + '\n')
        write(o / 'requirements.txt', f"apache-airflow=={AIRFLOW['airflow']}\napache-airflow-providers-oracle=={AIRFLOW['oracle']}\n"
              f"apache-airflow-providers-common-sql=={AIRFLOW['common-sql']}\noracledb=={AIRFLOW['oracledb']}\n")
        write(o / 'pyproject.toml', f'[project]\nname = "{ident(self.name)}-airflow"\nversion = "0.1.0"\nrequires-python = "=={AIRFLOW["python"]}.*"\n'
              f'\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
        write(o / 'docker-compose.yml', COMPOSE.replace('@AIRFLOW@', AIRFLOW['airflow']).replace('@PY@', AIRFLOW['python']))
        write(o / 'tests/dags/test_dag_integrity.py', DAG_TEST.replace('@EXPECTED@', json.dumps(sorted(d['dagId'] for d in self.dags))))
        specs = []
        for j in self.p.jobs:
            if j.job_type == 'PARALLEL' and (spec := reconciliation_spec(j)):
                specs += spec
                write(o / f'tests/reconciliation/{ident(j.name)}.sql', reconciliation_sql(spec))
        write(o / 'tests/reconciliation/reconciliation.json', json.dumps({'format': RECON_FORMAT, 'project': self.name, 'tables': specs},
                                                                        ensure_ascii=False, indent=2))
        write(o / 'tests/reconciliation/run_reconciliation.py', RECON_RUNNER.replace('@FORMAT@', RECON_FORMAT))
        write(o / 'tests/reconciliation/README.md', RECON_README)
        write(o / 'README.md', README.replace('@NAME@', self.name).replace('@AIRFLOW@', AIRFLOW['airflow']))


VOLATILE = re.compile(r'(?i)\b(current(timestamp|date|time)\w*|systimestamp|sysdate|current_timestamp|current_date)\b')
NUMERIC = ('2', '3', '4', '-5', '5', '6', '7', '8')
RECON_FORMAT = 'mf-datastage-reconciliation/1'
SCHEMA_MARK = '@SCHEMA@'          # replaced by the legacy or the migrated schema when the check runs
ROW_SEP = " || '|' || "           # row text for ORA_HASH: columns joined with a separator (a|bc differs from ab|c)


def reconciliation_spec(job) -> list[dict]:
    """What to compare for every Oracle table the job writes (doc 60): its columns, business key and numeric columns.
    Columns filled with the run time (CurrentTimestamp...) differ on every run by design: they are excluded and named."""
    out = []
    for s in job.stages:
        if s.stage_type != 'OracleConnectorPX' or not s.input_links or not s.properties.get('Usage/TableName'):
            continue
        link = next(l for l in job.links if l.name == s.input_links[0])
        table = Jinja(job)(s.properties['Usage/TableName']).rsplit('.', 1)[-1]  # the schema is the side being compared
        excluded = [c['name'] for c in link.columns if VOLATILE.search(str(c.get('derivation') or link.expressions.get(c['name']) or ''))]
        cols = [c['name'] for c in link.columns if c['name'] not in excluded]
        keys = [c['name'] for c in link.columns if c.get('key') and c['name'] in cols] or cols[:1]
        nums = [c['name'] for c in link.columns if str(c.get('sqlType')) in NUMERIC and c['name'] in cols]
        if cols:
            spec = {'job': job.name, 'stage': s.name, 'table': table, 'keys': keys, 'columns': cols, 'numeric': nums, 'excluded': excluded}
            spec['checks'] = [[name, q] for name, q in check_queries(spec, SCHEMA_MARK)]
            # key and hash of every row: the difference sample is built from these, also between two databases
            spec['rowHash'] = f"SELECT {ROW_SEP.join(keys)}, ORA_HASH({ROW_SEP.join(cols)}) FROM {SCHEMA_MARK}.{table}"
            out.append(spec)
    return out


def check_queries(spec: dict, schema: str) -> list[tuple[str, str]]:
    """(check, SELECT returning one value) over `schema`.table; the same list runs on both sides."""
    t, keys, cols = f'{schema}.{spec["table"]}', ', '.join(spec['keys']), spec['columns']
    row = ROW_SEP.join(cols)
    qs = [('row_count', f'SELECT COUNT(*) FROM {t}'),
          ('business_key_count', f'SELECT COUNT(*) FROM (SELECT DISTINCT {keys} FROM {t})'),
          ('duplicate_count', f'SELECT COUNT(*) FROM (SELECT {keys} FROM {t} GROUP BY {keys} HAVING COUNT(*) > 1)'),
          ('checksum', f'SELECT SUM(ORA_HASH({row})) FROM {t}')]
    qs += [(f'null_count:{c}', f'SELECT COUNT(*) - COUNT({c}) FROM {t}') for c in cols]
    qs += [(f'{f}:{c}', f'SELECT {f.upper()}({c}) FROM {t}') for c in spec['numeric'] for f in ('sum', 'min', 'max')]
    return qs


def reconciliation_sql(specs: list[dict]) -> str:
    """The same checks as run_reconciliation.py, as plain SQL for a person (replace the two schemas)."""
    out = []
    for spec in specs:
        out.append(f'-- {spec["job"]} / {spec["stage"]} -> {spec["table"]}\n'
                   f'-- Reemplazar LEGACY_SCHEMA (salida de DataStage) y TARGET_SCHEMA (salida de Airflow); misma entrada en los dos.\n'
                   + (f'-- Excluidas (se llenan con la hora de ejecución): {", ".join(spec["excluded"])}\n' if spec['excluded'] else ''))
        for side in ('LEGACY_SCHEMA', 'TARGET_SCHEMA'):
            out.append('\nUNION ALL\n'.join(f"SELECT '{side}' AS lado, '{name}' AS chequeo, ({q}) AS valor FROM dual"
                                            for name, q in check_queries(spec, side)) + ';\n')
        cols = ', '.join(spec['columns'])
        for a, b in (('LEGACY_SCHEMA', 'TARGET_SCHEMA'), ('TARGET_SCHEMA', 'LEGACY_SCHEMA')):
            out.append(f'-- filas en {a} que no están en {b} (hasta 50)\n'
                       f'SELECT * FROM (SELECT {cols} FROM {a}.{spec["table"]} MINUS SELECT {cols} FROM {b}.{spec["table"]}) '
                       f'FETCH FIRST 50 ROWS ONLY;\n')
    return '\n'.join(out)


RUNTIME = r'''"""Funciones reutilizables de los DAGs migrados desde DataStage (generado por Migration Factory)."""
from __future__ import annotations

import csv
import re
from pathlib import Path

import yaml
from airflow.exceptions import AirflowFailException

CONFIG = Path(__file__).resolve().parents[2] / "config"
_LITERAL = re.compile(r'^\s*("(?:[^"\\]|\\.)*"|\'[^\']*\'|-?\d+(?:\.\d+)?)\s*$')
_CMP = re.compile(r"^\s*(?:(\w+)\.)?(\w+)\s*(=|<>|<=|>=|<|>)\s*(.+?)\s*$")


def manual_step(job: str, stage: str, reason: str, **_):
    """Una etapa sin traducción verificada: falla en vez de ejecutar SQL incompleto."""
    raise AirflowFailException(f"MANUAL ({job}/{stage}): {reason}")


def terminate(message: str, **_):
    raise AirflowFailException(f"Secuencia terminada por DataStage TerminatorActivity: {message}")


def user_variables(activity: str, variables: dict, ti=None, **_):
    """Variables de usuario de una secuencia: las constantes se publican en XCom; las expresiones (rutinas de DataStage,
    contadores de registros) deben reimplementarse aquí y fallan hasta entonces."""
    for name, expr in variables.items():
        m = _LITERAL.match(expr or "")
        if not m:
            raise AirflowFailException(f"MANUAL ({activity}.{name}): reimplementar «{expr}»")
        value = m[1]
        ti.xcom_push(key=name, value=value[1:-1] if value[0] in "\"'" else float(value) if "." in value else int(value))


def branch(branches: list, activities: dict, ti=None, params=None, **_):
    """Condición de DataStage: rama personalizada (tipo 6) si su expresión es verdadera; si ninguna, las de tipo 0.
    `ACTIVIDAD.variable` se lee de XCom; un nombre solo es un parámetro del DAG."""
    chosen = []
    for b in branches:
        if b["type"] != "6":
            continue
        m = _CMP.match(b["expression"] or "")
        if not m:
            raise AirflowFailException(f"MANUAL: condición no soportada «{b['expression']}»")
        act, var, op, lit = m.groups()
        if act is None:
            if var not in (params or {}):
                raise AirflowFailException(f"MANUAL: «{var}» no es un parámetro del DAG")
            left = params[var]
        else:
            left = ti.xcom_pull(task_ids=activities.get(act, act), key=var)
        right = lit.strip('"').strip("'")
        numeric = re.fullmatch(r"-?\d+(\.\d+)?", right) is not None
        lv, rv = (float(left), float(right)) if numeric else (str(left), right)
        ok = {"=": lv == rv, "<>": lv != rv, "<": lv < rv, ">": lv > rv, "<=": lv <= rv, ">=": lv >= rv}[op]
        if ok:
            chosen.append(b["task"])
    return chosen or [b["task"] for b in branches if b["type"] == "0"]


def _file_format(stage: str) -> dict:
    cfg = yaml.safe_load((CONFIG / "files.yaml").read_text(encoding='utf-8')) or {}
    if stage not in cfg:
        raise AirflowFailException(f"MANUAL ({stage}): definir el formato del archivo en config/files.yaml")
    return cfg[stage]


def load_file(conn_id: str, stage: str, path: str, table: str, columns: list, **_):
    from airflow.providers.oracle.hooks.oracle import OracleHook
    fmt = _file_format(stage)
    hook = OracleHook(oracle_conn_id=conn_id)
    hook.run(f"TRUNCATE TABLE {table}")
    with open(path, newline="", encoding=fmt.get("encoding", "utf-8")) as fh:
        reader = csv.reader(fh, delimiter=fmt.get("delimiter", "|"), quotechar=fmt.get("quotechar") or None)
        if fmt.get("header"):
            next(reader, None)
        rows = [row + [n] for n, row in enumerate(reader, 1)]  # MF_SEQ conserva el orden de las líneas
    hook.insert_rows(table, rows, target_fields=[*columns, "MF_SEQ"], commit_every=10000)


def export_file(conn_id: str, stage: str, path: str, sql_file: str, **context):
    from airflow.providers.oracle.hooks.oracle import OracleHook
    fmt = _file_format(stage)
    sql = context["task"].render_template((Path(__file__).resolve().parents[2] / sql_file).read_text(encoding='utf-8'), context)
    rows = OracleHook(oracle_conn_id=conn_id).get_records(sql.rstrip().rstrip(";"))
    with open(path, "w", newline="", encoding=fmt.get("encoding", "utf-8")) as fh:
        csv.writer(fh, delimiter=fmt.get("delimiter", "|")).writerows(rows)
'''

DAG_TEST = '''"""Integridad de los DAGs (DAG_PARSE_GATE): importan sin errores y son los esperados."""
import json
from pathlib import Path

from airflow.models import DagBag

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = json.loads(\'@EXPECTED@\')


def test_dags_import_without_errors():
    bag = DagBag(dag_folder=str(ROOT / "dags"), include_examples=False)
    assert bag.import_errors == {}
    assert sorted(bag.dag_ids) == EXPECTED
    for dag in bag.dags.values():
        assert dag.tasks, dag.dag_id
'''

COMPOSE = '''# Airflow local para revisar los DAGs migrados (no para producción). Las credenciales van en Connections.
services:
  airflow:
    image: apache/airflow:@AIRFLOW@-python@PY@
    command: standalone
    ports: ["127.0.0.1:8080:8080"]
    environment:
      AIRFLOW__CORE__LOAD_EXAMPLES: "false"
      _PIP_ADDITIONAL_REQUIREMENTS: "apache-airflow-providers-oracle==4.0.0"
    volumes:
      - ./dags:/opt/airflow/dags
      - ./src:/opt/airflow/src
      - ./sql:/opt/airflow/sql
      - ./config:/opt/airflow/config
'''

RECON_README = '''# Reconciliación DataStage → Airflow

Compara cada tabla que escribe un job en el esquema legado (salida de DataStage) contra la del esquema migrado (salida de
Airflow), con la misma entrada: conteo de filas, claves de negocio, duplicados, nulos por columna, suma/mínimo/máximo de
las numéricas, checksum y una muestra de diferencias en los dos sentidos. Las columnas que se llenan con la hora de
ejecución (CurrentTimestamp...) se excluyen y se nombran: difieren en cada corrida por diseño.

1. Correr el job en DataStage escribiendo en el esquema legado y el DAG en Airflow escribiendo en el migrado.
2. En el ambiente de Airflow (las credenciales salen de sus Connections):

       python tests/reconciliation/run_reconciliation.py --conn oracle_p_conn --legacy DS_LEGADO --target AF_MIGRADO

   Con dos bases distintas: `--legacy-conn` y `--target-conn`.
3. Subir `reconciliation-result.json` a Migration Factory (DataStage → Reconciliación): ahí queda la evidencia.

`reconciliation.json` tiene las consultas exactas; `<job>.sql`, las mismas para correrlas a mano.
Estado mientras no se ejecuten: `RECONCILIATION_NOT_EXECUTABLE` (el DAG que importa no prueba equivalencia funcional).
'''

RECON_RUNNER = '''"""Reconciliación DataStage -> Airflow (generado por Migration Factory). Ver README.md de esta carpeta.

Ejecuta las consultas de reconciliation.json en el esquema legado y en el migrado con Connections de Airflow (aquí no se
escriben credenciales) y deja reconciliation-result.json para subirlo a Migration Factory. Sale con 0 solo si todo es igual.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FORMAT = "@FORMAT@"
SCHEMA = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
SAMPLE = 50              # claves de ejemplo por tipo de diferencia
MAX_ROWS = 2_000_000     # filas por lado para la muestra de diferencias


def connect(conn_id: str):
    from airflow.providers.oracle.hooks.oracle import OracleHook
    return OracleHook(oracle_conn_id=conn_id).get_conn()


def text(v):
    return None if v is None else v.isoformat() if hasattr(v, "isoformat") else str(v)


def row_hashes(cur, sql: str) -> tuple[dict, bool]:
    cur.execute(f"{sql} FETCH FIRST {MAX_ROWS + 1} ROWS ONLY")
    out, n = {}, 0
    for key, h in cur:
        n += 1
        if n > MAX_ROWS:
            return out, True
        out.setdefault(text(key), []).append(text(h))
    return out, False


def table(spec: dict, sides: dict) -> dict:
    res = {k: spec[k] for k in ("job", "stage", "table", "keys", "excluded")}
    try:
        checks, hashes = [], {}
        for side, (conn, schema) in sides.items():
            cur = conn.cursor()
            values = []
            for name, q in spec["checks"]:
                cur.execute(q.replace("@SCHEMA@", schema))
                values.append(text(cur.fetchone()[0]))
            checks.append(values)
            hashes[side] = row_hashes(cur, spec["rowHash"].replace("@SCHEMA@", schema))
        res["checks"] = [{"name": n, "legacy": a, "target": b} for (n, _), a, b in zip(spec["checks"], *checks)]
        (old, cut_a), (new, cut_b) = hashes["legacy"], hashes["target"]
        res["diff"] = {"onlyLegacy": sorted(k for k in old if k not in new)[:SAMPLE],
                       "onlyTarget": sorted(k for k in new if k not in old)[:SAMPLE],
                       "changed": sorted(k for k in old if k in new and sorted(old[k]) != sorted(new[k]))[:SAMPLE],
                       "truncated": cut_a or cut_b}
        same = all(c["legacy"] == c["target"] for c in res["checks"]) and not any(res["diff"][k] for k in ("onlyLegacy", "onlyTarget", "changed"))
        res["verdict"] = "EQUAL" if same else "DIFFERENT"
    except Exception as e:  # noqa: BLE001 - one table failing (no access, missing table) does not stop the others
        res["verdict"], res["error"] = "ERROR", f"{type(e).__name__}: {e}"[:500]
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--conn", help="Connection de Airflow para los dos esquemas")
    ap.add_argument("--legacy-conn")
    ap.add_argument("--target-conn")
    ap.add_argument("--legacy", required=True, help="esquema con la salida de DataStage")
    ap.add_argument("--target", required=True, help="esquema con la salida de Airflow")
    ap.add_argument("--only", action="append", default=[], help="solo estos jobs")
    ap.add_argument("--out", default="reconciliation-result.json")
    a = ap.parse_args(argv)
    lc, tc = a.legacy_conn or a.conn, a.target_conn or a.conn
    if not lc or not tc:
        ap.error("indique --conn, o --legacy-conn y --target-conn")
    for s in (a.legacy, a.target):
        if not SCHEMA.match(s):
            ap.error(f"esquema no válido: {s}")
    spec = json.loads((HERE / "reconciliation.json").read_text(encoding="utf-8"))
    conns = {lc: connect(lc)}
    conns.setdefault(tc, connect(tc))
    sides = {"legacy": (conns[lc], a.legacy), "target": (conns[tc], a.target)}
    tables = [table(t, sides) for t in spec["tables"] if not a.only or t["job"] in a.only]
    result = {"format": FORMAT, "project": spec["project"], "executedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "legacy": {"conn": lc, "schema": a.legacy}, "target": {"conn": tc, "schema": a.target}, "tables": tables}
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for t in tables:
        print(f"{t['verdict']:9} {t['job']} / {t['table']}" + (f"  ({t['error']})" if t.get("error") else ""))
    print(f"Resultado: {a.out}")
    return 0 if tables and all(t["verdict"] == "EQUAL" for t in tables) else 1


if __name__ == "__main__":
    sys.exit(main())
'''

README = '''# @NAME@ en Apache Airflow

Generado por Migration Factory desde exportaciones de IBM DataStage (DSX). Airflow @AIRFLOW@.

- `dags/`: un DAG por secuencia maestra (y por job sin secuencia). Cada job paralelo es un TaskGroup.
- `sql/`: SQL por etapa escritora (pushdown a Oracle) y `sql/ddl/staging.sql` (tablas que reemplazan a los DataSets).
- `src/`: funciones reutilizables (archivos planos, condiciones, pasos manuales).
- `config/`: `connection-manifest.json` (sin secretos), `variables.json` (por ambiente), `files.yaml`, `schedules.yaml`.
- `tests/dags`: integridad con DagBag. `tests/reconciliation`: comparación contra la salida de DataStage.
- `migration-report/`: inventario, grafo de dependencias, decisiones por etapa, acciones manuales y el IR (`datastage-ir.json`).

Las tareas marcadas MANUAL fallan a propósito con el motivo: no se ejecuta SQL a medio traducir.
'''

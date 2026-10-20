"""Quality gates (docs 60-61). A gate is PASS, WARN, FAIL or NOT_EXECUTABLE; NOT_EXECUTABLE never counts as PASS.
DAG_PARSE_GATE uses Airflow's DagBag in a separate interpreter (MF_AIRFLOW_PYTHON) so the platform never imports the
generated code; without that interpreter the gate is NOT_EXECUTABLE."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from ..security import find_secrets

_UNRESOLVED = re.compile(r'#[A-Za-z_]\w*(?:\.\$?\w+)?#')

DAGBAG = r'''
import json, sys
from airflow.models import DagBag
bag = DagBag(dag_folder=sys.argv[1], include_examples=False)
print(json.dumps({"errors": {k: v[-2000:] for k, v in bag.import_errors.items()},
                  "dags": {d.dag_id: {"tasks": len(d.tasks), "edges": sum(len(t.downstream_task_ids) for t in d.tasks)} for d in bag.dags.values()}}))
'''


def gate(code, status, detail='', **extra):
    return {'gate': code, 'status': status, 'detail': detail} | extra


def dsx_parse(project):
    st = project.metadata['status']
    warns = project.metadata['warnings'] + [f'{j.name}: {w}' for j in project.jobs for w in j.warnings]
    return gate('DSX_PARSE_GATE', {'PARSED': 'PASS', 'PARSED_WITH_WARNINGS': 'WARN', 'PARTIAL': 'WARN'}.get(st, 'FAIL'),
                f'{st}: {len(project.jobs)} job(s), {len(warns)} advertencia(s)', warnings=warns[:200])


def dependency(analysis):
    g = analysis['graph']
    bad = analysis['missingJobs']
    st = 'FAIL' if g['cycles'] else 'WARN' if bad or g['droppedEdges'] else 'PASS'
    return gate('DEPENDENCY_GRAPH_GATE', st, f"{len(g['edges'])} aristas, {len(g['waves'])} oleadas, ciclos: {len(g['cycles'])}, "
                f"jobs llamados que no están: {len(bad)}, aristas de datos descartadas: {len(g['droppedEdges'])}", missingJobs=bad)


def mapping(analysis):
    unsupported = [f"{d['job']}/{d['stage']} ({d['stage_type']})" for v in analysis['decisions'].values() for d in v if d['status'] == 'UNSUPPORTED']
    manual = sum(1 for v in analysis['decisions'].values() for d in v if d['status'] in ('MANUAL_REVIEW', 'ARCHITECTURAL_REDESIGN'))
    return gate('STAGE_MAPPING_GATE', 'FAIL' if unsupported else 'WARN' if manual else 'PASS',
                f'{len(unsupported)} etapa(s) sin mapeo, {manual} con revisión manual', unsupported=unsupported)


def secrets(out: Path, literal_credentials: list):
    hits = []
    for p in sorted(out.rglob('*')):
        if p.is_file() and p.suffix in ('.py', '.sql', '.json', '.yaml', '.yml', '.md', '.txt', '.toml'):
            if find_secrets(p.read_text(encoding='utf-8', errors='ignore')):
                hits.append(p.relative_to(out).as_posix())
    st = 'FAIL' if hits else 'WARN' if literal_credentials else 'PASS'
    return gate('SECRET_GATE', st, f'{len(hits)} archivo(s) generados con valores secretos; {len(literal_credentials)} credencial(es) '
                'literal(es) en el DSX (SECRET_FINDING, no copiadas)', files=hits, secretFindings=literal_credentials)


def connection_manifest(out: Path):
    data = json.loads((out / 'config/connection-manifest.json').read_text(encoding='utf-8'))
    leaked = [c['connId'] for c in data['connections'] if 'value' in (c['fields'].get('password') or {})]
    return gate('CONNECTION_MANIFEST_GATE', 'FAIL' if leaked else 'PASS', f"{len(data['connections'])} conexión(es); sin contraseñas"
                if not leaked else f'contraseñas en {leaked}')


def dag_parse(out: Path, airflow_python: str | None = None):
    dags = sorted((out / 'dags').glob('*.py'))
    errors = {}
    for d in dags:
        try:
            compile(d.read_text(encoding='utf-8'), str(d), 'exec')  # syntax only; nothing is imported or executed here
        except SyntaxError as e:
            errors[d.name] = str(e)[:500]
    if errors:
        return gate('DAG_PARSE_GATE', 'FAIL', f'{len(errors)} DAG(s) con error de sintaxis', state='DAG_PARSE_FAIL', errors=errors)
    py = airflow_python or os.environ.get('MF_AIRFLOW_PYTHON')
    if not py or not Path(py).exists():
        return gate('DAG_PARSE_GATE', 'NOT_EXECUTABLE', f'{len(dags)} DAG(s) compilan; DagBag no disponible (MF_AIRFLOW_PYTHON)',
                    state='DAG_PARSE_NOT_EXECUTED')
    home = out.parent / '.airflow-home'
    env = {'PATH': os.environ.get('PATH', ''), 'AIRFLOW_HOME': str(home), 'AIRFLOW__CORE__LOAD_EXAMPLES': 'False',
           'AIRFLOW__CORE__UNIT_TEST_MODE': 'True', 'PYTHONDONTWRITEBYTECODE': '1', 'AIRFLOW__DATABASE__SQL_ALCHEMY_CONN': f'sqlite:///{home}/airflow.db'}
    r = subprocess.run([py, '-c', DAGBAG, str(out / 'dags')], capture_output=True, text=True, timeout=600, env=env)
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return gate('DAG_PARSE_GATE', 'FAIL', f'DagBag no respondió: {r.stderr[-800:]}', state='DAG_PARSE_FAIL')
    st = 'FAIL' if res['errors'] or len(res['dags']) != len(dags) else 'PASS'
    return gate('DAG_PARSE_GATE', st, f"DagBag: {len(res['dags'])} DAG(s), {len(res['errors'])} error(es) de importación",
                state='DAG_PARSE_PASS' if st == 'PASS' else 'DAG_PARSE_FAIL', dags=res['dags'], errors=res['errors'])


def sql_files(out: Path, results: dict):
    """Structural checks only (balanced parentheses and quotes, no DataStage #PARAM# left, no empty statement).
    Without executing against Oracle the SQL stays SQL_UNVERIFIED."""
    problems, n = [], 0
    for p in sorted((out / 'sql').rglob('*.sql')):
        n += 1
        text = '\n'.join(l for l in p.read_text(encoding='utf-8').splitlines() if not l.lstrip().startswith('--'))
        if not text.strip():
            continue
        rel = p.relative_to(out).as_posix()
        if text.count('(') != text.count(')'):
            problems.append(f'{rel}: paréntesis desbalanceados')
        if re.sub(r"''", '', text).count("'") % 2:
            problems.append(f'{rel}: comillas desbalanceadas')
        if _UNRESOLVED.search(re.sub(r'\{\{.*?\}\}', '', text)):
            problems.append(f'{rel}: parámetro de DataStage sin resolver')
    return gate('SQL_VALIDATION_GATE', 'FAIL' if problems else 'PASS', f'{n} archivo(s) SQL, {len(problems)} con problemas estructurales; '
                'sin ejecutar contra Oracle', state='SQL_UNVERIFIED', problems=problems)


def reconciliation(out: Path):
    files = sorted((out / 'tests/reconciliation').glob('*.sql'))
    return gate('RECONCILIATION_GATE', 'NOT_EXECUTABLE', f'{len(files)} archivo(s) de reconciliación generados; requieren ejecutar DataStage '
                'y Airflow sobre la misma entrada', state='RECONCILIATION_NOT_EXECUTABLE', files=[f.name for f in files])

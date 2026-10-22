"""DataStage -> Airflow pipeline (doc 51): DSX/ZIP -> parse -> IR -> analysis -> registry -> generation -> gates -> report ->
Airflow ZIP. Shared by the CLI (`migrate.py datastage`) and the worker. The source is never modified and nothing from
it is executed."""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import validation
from .analysis import analyze
from .generation import AIRFLOW, Project, connections
from .parser import parse_files

MIGRATION_TYPE = 'DATASTAGE_AIRFLOW'
JOB_STATES = ('AUTO_MIGRATED', 'SEMIAUTOMATIC', 'MANUAL_REVIEW', 'BLOCKED', 'FAILED')


class InvalidInput(Exception):
    pass


def discover(source: Path) -> list[Path]:
    """`.dsx` files under a folder (or a ZIP already extracted by the caller)."""
    if source.is_file() and source.suffix.lower() == '.dsx':
        return [source]
    files = sorted(p for p in source.rglob('*') if p.is_file() and p.suffix.lower() == '.dsx' and not p.is_symlink())
    if not files:
        raise InvalidInput(f'No hay archivos .dsx en {source}')
    return files


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyze_only(source: Path, name: str):
    files = discover(source)
    root = source if source.is_dir() else source.parent
    project = parse_files(files, name, root)
    return project, analyze(project), files, root


def migrate(source: Path, out: Path, name: str | None = None, airflow_python: str | None = None, timezone_name='America/Mexico_City',
            log=lambda m: None, schedules: dict | None = None) -> dict:
    source, out = Path(source).resolve(), Path(out).resolve()
    if out == source or source in out.parents:
        raise InvalidInput('La salida no puede estar dentro del origen (el origen nunca se modifica)')
    name = name or (source.stem if source.is_file() else source.name)
    log('leyendo DSX')
    project, analysis, files, root = analyze_only(source, name)
    log(f"{len(project.jobs)} job(s) en {len(files)} archivo(s): {project.metadata['status']}")
    proj_dir = out / f'airflow-{Path(name).stem.lower().replace(" ", "-")}'
    if proj_dir.exists():
        shutil.rmtree(proj_dir)
    report_dir = proj_dir / 'migration-report'
    report_dir.mkdir(parents=True)
    (report_dir / 'datastage-ir.json').write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=1), encoding='utf-8')
    log('generando DAGs, SQL y configuración')
    gen = Project(project, analysis, proj_dir, name, timezone_name, schedules)
    results = gen.generate()
    conns = connections(project)
    literal = [x for c in conns for x in c['literalCredentials']]
    log('validando')
    gates = [validation.dsx_parse(project), validation.dependency(analysis), validation.mapping(analysis),
             validation.secrets(proj_dir, literal), validation.connection_manifest(proj_dir),
             validation.dag_parse(proj_dir, airflow_python), validation.sql_files(proj_dir, results), validation.reconciliation(proj_dir)]
    for j in analysis['jobs']:  # a sequence whose jobs are missing is blocked by its dependency
        r = results.get(j['job'])
        if r is None:
            results[j['job']] = {'state': 'FAILED', 'reason': 'sin salida generada'}
    counts = {s: sum(1 for r in results.values() if r['state'] == s) for s in JOB_STATES}
    by_gate = {g['gate']: g['status'] for g in gates}
    if project.metadata['status'] == 'FAILED' or by_gate.get('DAG_PARSE_GATE') == 'FAIL' or by_gate.get('SECRET_GATE') == 'FAIL':
        overall = 'FAILED'
    elif counts['BLOCKED'] == len(results):
        overall = 'BLOCKED'
    elif (counts['AUTO_MIGRATED'] == len(results) and all(s == 'PASS' for s in by_gate.values())):
        overall = 'SUCCEEDED'
    else:
        overall = 'PARTIAL_SUCCESS'  # reconciliation not executed => functionally unverified, never SUCCEEDED
    manual = sorted({m for x in analysis['jobs'] for m in x['manualActions']}
                    | {f"{j}/{s['task']}: {'; '.join(s['issues'])}" for j, r in results.items() for s in r.get('steps', []) if s['status'] == 'MANUAL'})
    report = {'migrationType': MIGRATION_TYPE, 'name': name, 'status': overall, 'functionalValidation': 'FUNCTIONALLY_UNVERIFIED',
              'technicalValidation': 'TECHNICALLY_VALID' if all(by_gate[g] in ('PASS', 'WARN') for g in
                                                                ('DSX_PARSE_GATE', 'SECRET_GATE', 'DAG_PARSE_GATE', 'SQL_VALIDATION_GATE')) else 'NOT_VERIFIED',
              'parse': {'status': project.metadata['status'], 'files': len(files), 'jobs': len(project.jobs),
                        'sequences': len(project.sequences), 'dataStageVersion': project.metadata['dataStageVersion']},
              'counts': counts, 'gates': gates, 'jobs': {x['job']: x | {'migration': results[x['job']]} for x in analysis['jobs']},
              'graph': analysis['graph'], 'pilot': analysis['pilot'], 'manualActions': manual, 'actions': worklist(analysis, results),
              'dags': gen.dags,
              'airflow': AIRFLOW, 'generatedAt': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    (report_dir / 'inventory.json').write_text(json.dumps(analysis['jobs'], ensure_ascii=False, indent=1), encoding='utf-8')
    (report_dir / 'dependency-graph.json').write_text(json.dumps(analysis['graph'], ensure_ascii=False, indent=1), encoding='utf-8')
    (report_dir / 'stage-decisions.json').write_text(json.dumps(analysis['decisions'], ensure_ascii=False, indent=1), encoding='utf-8')
    (report_dir / 'migration-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    (report_dir / 'migration-report.md').write_text(markdown(report), encoding='utf-8')
    (report_dir / 'manual-actions.md').write_text('# Acciones manuales\n\n' + ''.join(f'- [ ] {m}\n' for m in manual), encoding='utf-8')
    hashes = {f'source/{p.relative_to(root).as_posix()}': sha(p) for p in files}
    hashes |= {p.relative_to(proj_dir).as_posix(): sha(p) for p in sorted(proj_dir.rglob('*')) if p.is_file()}
    manifest = {'sourceType': 'IBM_DATASTAGE_DSX', 'migrationType': MIGRATION_TYPE, 'dataStageVersion': project.metadata['dataStageVersion'],
                'target': f"Apache Airflow {AIRFLOW['airflow']}", 'jobCount': len(project.jobs), 'autoMigrated': counts['AUTO_MIGRATED'],
                'semiautomatic': counts['SEMIAUTOMATIC'], 'manualReview': counts['MANUAL_REVIEW'], 'blocked': counts['BLOCKED'],
                'failed': counts['FAILED'], 'status': overall, 'hashes': hashes, 'timestamp': report['generatedAt']}
    (proj_dir / '.migration').mkdir(exist_ok=True)
    (proj_dir / '.migration/manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    zip_path = out / f'{proj_dir.name}.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(proj_dir.rglob('*')):
            if p.is_file():
                z.write(p, f'{proj_dir.name}/{p.relative_to(proj_dir).as_posix()}')
    with zipfile.ZipFile(zip_path) as z:  # PACKAGE_GATE: every generated file is in the ZIP with the manifest's hash
        names = set(z.namelist())
        missing = [k for k in hashes if not k.startswith('source/') and k != '.migration/manifest.json' and f'{proj_dir.name}/{k}' not in names]
        bad = [k for k in hashes if not k.startswith('source/') and k != '.migration/manifest.json' and f'{proj_dir.name}/{k}' in names
               and hashlib.sha256(z.read(f'{proj_dir.name}/{k}')).hexdigest() != hashes[k]]
    report['gates'].append(validation.gate('PACKAGE_GATE', 'FAIL' if missing or bad else 'PASS', f'{len(names)} archivo(s) en {zip_path.name}',
                                           missing=missing, mismatched=bad))
    if missing or bad:
        report['status'] = 'FAILED'
    (report_dir / 'migration-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    (report_dir / 'migration-report.md').write_text(markdown(report), encoding='utf-8')
    report['output'] = {'project': str(proj_dir), 'zip': str(zip_path)}
    return report


CATEGORIES = (  # first match wins; the UI groups the worklist by these
    ('PL/SQL', r'PL/SQL|WriteMode 8'),
    ('Lógica entre filas', r'fila anterior|estado acumulado|entre filas|orden de filas|LAG\(|@PARTITION'),
    ('Rutinas de DataStage', r'rutina|reimplementar'),
    ('Archivos planos', r'archivo|files\.yaml|delimitador'),
    ('Join / Merge', r'Merge|JOIN|join|maestro|rechazo'),
    ('Duplicados y orden', r'duplicad|RemDup|Sort previo|«first»'),
    ('Expresiones del Transformer', r'expresi[oó]n'),
    ('Comandos de shell', r'comando|BashOperator'),
    ('Generadores de datos', r'Generator|generad'),
    ('Condiciones y filtros', r'condici|WHERE|Filter'),
    ('Rediseño', r'sin equivalente|rediseñ|sin mapeo'),
    ('Tablas y SQL', r'TableAction|SQL|tabla|clave'),
)


def worklist(analysis: dict, results: dict) -> list[dict]:
    """Manual actions as a structured worklist (job, stage, category, severity) with a stable key, so a team can mark them
    done across runs of the same project. manual = the step does not run until it is done; review = runs, confirm it."""
    import re
    out, seen = [], set()

    def add(job, stage, text, severity, source):
        if text.startswith(stage + ': '):  # the stage is its own column
            text = text[len(stage) + 2:]
        key = hashlib.sha1(f'{job}|{stage}|{text}'.encode()).hexdigest()[:16]
        if key in seen:
            return
        seen.add(key)
        cat = next((c for c, rx in CATEGORIES if re.search(rx, text)), 'Otros')
        out.append({'key': key, 'job': job, 'stage': stage, 'category': cat, 'severity': severity, 'source': source, 'text': text})
    for job, decisions in analysis['decisions'].items():
        for d in decisions:
            for m in d['manual_actions']:
                add(job, d['stage'], m.split(': ', 1)[-1] if m.startswith(d['stage'] + ':') else m,
                    'manual' if d['status'] in ('MANUAL_REVIEW', 'UNSUPPORTED', 'ARCHITECTURAL_REDESIGN') else 'review', 'registry')
    for job, r in results.items():
        for s in r.get('steps', []):
            if s['status'] in ('MANUAL', 'REVIEW'):
                for issue in s['issues']:
                    add(job, s['stage'], issue, 'manual' if s['status'] == 'MANUAL' else 'review', 'generation')
    return sorted(out, key=lambda a: (a['severity'] != 'manual', a['category'], a['job'], a['stage']))


def markdown(r: dict) -> str:
    p, c = r['parse'], r['counts']
    out = [f"# Migración DataStage → Airflow: {r['name']}", '', f"Estado: **{r['status']}** · validación técnica: {r['technicalValidation']} · "
           f"validación funcional: {r['functionalValidation']}", '',
           f"DSX: {p['files']} archivo(s), {p['jobs']} job(s) ({p['sequences']} secuencias), DataStage {p['dataStageVersion']}, parseo {p['status']}.", '',
           '| Estado del job | Jobs |', '|---|---|'] + [f'| {k} | {v} |' for k, v in c.items()]
    out += ['', '## Gates', '', '| Gate | Estado | Detalle |', '|---|---|---|'] + [f"| {g['gate']} | {g['status']} | {g['detail']} |" for g in r['gates']]
    out += ['', '## Piloto recomendado (doc 67)', ''] + [f'- {k}: `{v}`' for k, v in r['pilot'].items()]
    out += ['', '## Oleadas', ''] + [f"- Oleada {w['wave']}: {', '.join(w['jobs'])}" for w in r['graph']['waves']]
    if r['graph']['droppedEdges']:
        out += ['', 'Aristas de datos descartadas (contradecían el orden explícito de una secuencia):', '']
        out += [f"- {d['from']} → {d['to']} ({d['resource']})" for d in r['graph']['droppedEdges']]
    out += ['', '## Jobs', '', '| Job | Tipo | Complejidad (puntos) | Migrabilidad | Confianza | Resultado |', '|---|---|---|---|---|---|']
    out += [f"| {n} | {j['jobType']} | {j['complexity']} ({j['score']}) | {j['migratability']} | {j['confidence']} | {j['migration']['state']} |"
            for n, j in sorted(r['jobs'].items())]
    out += ['', '## Acciones manuales', ''] + [f'- [ ] {m}' for m in r['manualActions']]
    return '\n'.join(out) + '\n'

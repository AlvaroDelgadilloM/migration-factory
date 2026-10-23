"""Pruebas: manual requests and automatic comparisons of the original system against the migrated one (docs/19).

Automatic, from a migration (the default): the worker starts the original code on Camel 2 and the migration's WAR in Tomcat
itself, reads the cases from the code and compares; the user only picks the migration (and optionally the container and the
client's test data package). Automatic or manual against systems already running: every base URL must be on
MF_TEST_ALLOWED_HOSTS (SSRF). A manual request is one short HTTP call and runs here; an automatic run is a job."""
import uuid
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, jobs
from ..auth import Principal, authorize, current_principal
from ..config import get_settings
from ..db import get_db
from ..errors import ApiError
from ..models import Artifact, Execution, HttpTestRun, Job, Project, Route, Plan, Scan, WorkspaceMigration
from ..schemas import DataPackageOut, HttpTestRunIn, HttpTestRunOut, HttpTestSource, JobOut, ManualTestIn
from ..security import UnsafeInput, test_host_allowed, validate_test_url
from ..storage import ArtifactStore
from .. import runtime_test
from ..tomcat_war import autotest, http_run

router = APIRouter(tags=['pruebas'])
DATA_PACKAGE_MAX = 100 * 1024 * 1024


def _project(db, me, project_id, perm):
    if db.get(Project, project_id) is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, project_id, perm)


def _targets(targets) -> list[dict]:
    try:
        return [{'label': t.label, 'url': validate_test_url(t.url)} for t in targets]
    except UnsafeInput as e:
        raise ApiError(422, e.code, str(e)) from None


def _out(db, run: HttpTestRun) -> dict:
    d = HttpTestRunOut.model_validate(run).model_dump(mode='json')
    d['cases'] = [c | {'headers': http_run.mask(c.get('headers'))} for c in d['cases']]  # credentials of the cases never go back out
    job = db.get(Job, run.job_id) if run.job_id else None
    d['job'] = JobOut.model_validate(job).model_dump(mode='json') if job else None
    return d


def war_artifact(db, wm: WorkspaceMigration, container: str | None = None) -> Artifact | None:
    """The WAR to test: the container's own one when the migration had to split them (duplicate classes between containers,
    mf.tomcat_war.pipeline.container_wars), otherwise the single WAR."""
    wars = {k: v for k, v in ((wm.summary or {}).get('artifacts') or {}).items() if k.endswith('.war')}
    own = [v for k, v in wars.items() if container and k.endswith(f'.webapp-{container}.ROOT.war')]
    single = [v for k, v in wars.items() if '.webapp-' not in k]
    ids = own or single
    return db.get(Artifact, uuid.UUID(ids[0])) if ids else None


def _containers(wm: WorkspaceMigration) -> list[str]:
    c = (wm.config or {}).get('containers') or {}
    return list(c.get('names') or []) if isinstance(c, dict) else []


@router.get('/projects/{project_id}/http-tests/sources', response_model=list[HttpTestSource])
def sources(project_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    """Tomcat WAR migrations of the project, newest first, with what an automatic test needs."""
    _project(db, me, project_id, 'view')
    rows = db.scalars(select(WorkspaceMigration).where(WorkspaceMigration.project_id == project_id, WorkspaceMigration.target == 'tomcat-war')
                      .order_by(WorkspaceMigration.created_at.desc()).limit(20))
    return [{'workspace_migration_id': w.id, 'scan_id': w.scan_id, 'created_at': w.created_at, 'state': w.state,
             'build': ((w.summary or {}).get('build') or {}).get('status'), 'containers': _containers(w), 'war': war_artifact(db, w) is not None}
            for w in rows]


def candidate_artifact(db, ex: Execution) -> Artifact | None:
    return db.scalar(select(Artifact).where(Artifact.execution_id == ex.id, Artifact.kind == 'candidate-zip'))


def candidate_spec(db, ex: Execution) -> dict:
    """Runtime, variables and Camel HTTP base of an applied execution's candidate (read from its candidate.zip)."""
    import tempfile
    from ..security import safe_extract_zip
    art = candidate_artifact(db, ex)
    if art is None:
        raise ApiError(409, 'INVALID_STATE', 'La ejecución no tiene proyecto migrado (aplíquela primero)')
    with tempfile.TemporaryDirectory() as t:
        z = Path(t) / 'candidate.zip'
        z.write_bytes(ArtifactStore().read(art))
        try:
            root = Path(t) / 'p' / safe_extract_zip(z, Path(t) / 'p')
        except UnsafeInput as e:
            raise ApiError(422, e.code, str(e)) from None
        mod = runtime_test.module(root)
        kind = runtime_test.runtime(mod)
        return {'runtime': kind, 'variables': runtime_test.variables(mod), 'basePath': runtime_test.base_path(kind, mod, _war_name(mod)),
                'module': mod.relative_to(root).as_posix() if mod != root else '.'}


def _war_name(root: Path) -> str:
    import re
    pom = (root / 'pom.xml').read_text(encoding='utf-8', errors='replace') if (root / 'pom.xml').is_file() else ''
    m = re.search(r'<finalName>([\w.\-]+)</finalName>', pom) or re.search(r'<artifactId>([\w.\-]+)</artifactId>\s*<version>', pom)
    return m[1] if m else 'ROOT'


@router.get('/executions/{execution_id}/runtime-test')
def runtime_test_spec(execution_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    """What a run of this migrated project needs: its runtime, the variables to fill (with hints) and the endpoints the
    analysis saw (the run maps the real ones from the Camel startup log)."""
    ex = db.get(Execution, execution_id)
    if ex is None or ex.mode != 'apply':
        raise ApiError(404, 'NOT_FOUND', 'Ejecución aplicada no encontrada')
    _project(db, me, ex.project_id, 'view')
    spec = candidate_spec(db, ex)
    scan_id = db.get(Plan, ex.plan_id).scan_id
    routes = db.scalars(select(Route).where(Route.scan_id == scan_id))
    spec['analysisEndpoints'] = sorted({r.from_uri for r in routes if r.from_uri and r.from_uri.split(':', 1)[0] in ('rest', 'servlet', 'platform-http', 'cxf', 'cxfrs')})[:200]
    spec['allowedHosts'] = get_settings().list_of(get_settings().test_allowed_hosts)
    return spec


@router.get('/projects/{project_id}/http-tests/executions')
def executions_to_test(project_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    """Applied executions of the project with a migrated project to start, newest first."""
    _project(db, me, project_id, 'view')
    rows = db.scalars(select(Execution).where(Execution.project_id == project_id, Execution.mode == 'apply')
                      .order_by(Execution.created_at.desc()).limit(20))
    return [{'executionId': str(e.id), 'createdAt': e.created_at.isoformat(), 'state': e.state, 'status': (e.summary or {}).get('status'),
             'candidate': candidate_artifact(db, e) is not None} for e in rows]


@router.post('/projects/{project_id}/http-tests/data-packages', status_code=201, response_model=DataPackageOut)
async def upload_data_package(project_id: uuid.UUID, request: Request, x_filename: str = Header('datos-prueba.zip', max_length=255),
                              db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    """The client's test data (autotest.DATA_PACKAGE): configuration, database scripts, simulated answers of external
    systems, parameter values. Checked here and stored as an artifact; only the worker opens it."""
    _project(db, me, project_id, 'execute')
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > DATA_PACKAGE_MAX:
            raise ApiError(413, 'PAYLOAD_TOO_LARGE', f'Máximo {DATA_PACKAGE_MAX // 1024 // 1024} MB')
    try:
        contents = autotest.inspect_data_package(bytes(data))
    except autotest.AutoTestError as e:
        raise ApiError(422, 'DATA_PACKAGE_INVALID', str(e)) from None
    name = 'datos-prueba-' + Path(unquote(x_filename)).name[:150]
    art = ArtifactStore().put(db, project_id=project_id, kind='test-data', name=name if name.endswith('.zip') else name + '.zip',
                              data=bytes(data), content_type='application/zip')
    audit.record(db, actor=me.subject, action='http_test.data_package', entity_type='artifact', entity_id=art.id, project_id=project_id,
                 details={'sha256': art.sha256, 'size': len(data)} | {k: (len(v) if isinstance(v, list) else v) for k, v in contents.items()},
                 request=request)
    db.commit()
    return {'id': art.id, 'name': art.name, 'size_bytes': art.size_bytes, 'contents': contents}


@router.post('/projects/{project_id}/http-tests/manual')
def manual(project_id: uuid.UUID, body: ManualTestIn, request: Request, db: Session = Depends(get_db),
           me: Principal = Depends(current_principal)):
    _project(db, me, project_id, 'execute')
    targets = _targets(body.targets)
    case = body.request.model_dump(mode='json', exclude_none=True)
    audit.record(db, actor=me.subject, action='http_test.manual', entity_type='project', entity_id=project_id, project_id=project_id,
                 details={'method': case['method'], 'path': case['path'][:300], 'targets': [t['url'] for t in targets]}, request=request)
    db.commit()
    return http_run.manual(case, targets, min(get_settings().test_timeout_seconds, 60))


@router.post('/projects/{project_id}/http-tests/runs', status_code=202)
def start_run(project_id: uuid.UUID, body: HttpTestRunIn, request: Request, idempotency_key: str | None = Header(None),
              db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    _project(db, me, project_id, 'execute')
    original = migrated = None
    if body.source == 'urls':
        original, migrated = _targets([body.original, body.migrated])
    elif body.source == 'execution':
        ex = db.get(Execution, body.execution_id)
        if ex is None or ex.project_id != project_id or ex.mode != 'apply':
            raise ApiError(404, 'NOT_FOUND', 'Ejecución aplicada no encontrada en este proyecto')
        spec = candidate_spec(db, ex)
        given = {k: v.model_dump(exclude_none=True) for k, v in body.variables.items()}
        try:  # same checks as the worker (secrets only by reference, hosts on the test allowlist); refs are resolved there
            runtime_test.resolve(spec['variables'], given, test_host_allowed, lambda ref: 'referencia')
        except runtime_test.RuntimeTestError as e:
            raise ApiError(422, 'VARIABLES_INVALID', str(e)) from None
        if body.original:
            original = _targets([body.original])[0]
    else:
        wm = db.get(WorkspaceMigration, body.workspace_migration_id)
        if wm is None or wm.project_id != project_id or wm.target != 'tomcat-war':
            raise ApiError(404, 'NOT_FOUND', 'Migración Tomcat WAR no encontrada en este proyecto')
        if war_artifact(db, wm) is None:
            raise ApiError(409, 'INVALID_STATE', 'Esa migración no tiene WAR compilado: no se puede levantar')
        if body.data_package_id:
            art = db.get(Artifact, body.data_package_id)
            if art is None or art.project_id != project_id or art.kind != 'test-data':
                raise ApiError(404, 'NOT_FOUND', 'Paquete de datos no encontrado en este proyecto')
    payload = body.model_dump(mode='json')
    endpoint = f'POST /projects/{project_id}/http-tests/runs'
    replay = jobs.idempotent_replay(db, me.subject, idempotency_key, endpoint, payload)
    if replay:
        return replay
    if db.scalar(select(HttpTestRun.id).where(HttpTestRun.project_id == project_id, HttpTestRun.state.in_(('queued', 'running')))):
        raise ApiError(409, 'INVALID_STATE', 'Ya hay una prueba automática en curso para este proyecto')
    name = body.name or (f"Proyecto migrado {str(body.execution_id)[:8]}" if body.source == 'execution' else
                         f"Migración {str(body.workspace_migration_id)[:8]}" + (f' · {body.container}' if body.container else ''))
    run = HttpTestRun(project_id=project_id, name=name, source=body.source, workspace_migration_id=body.workspace_migration_id,
                      execution_id=body.execution_id, variables={k: v.model_dump(exclude_none=True) for k, v in body.variables.items()},
                      params=dict(body.params),
                      container=body.container, data_artifact_id=body.data_package_id, original=original, migrated=migrated,
                      screenshots=body.screenshots, cases=[c.model_dump(mode='json', exclude_none=True) for c in body.cases],
                      created_by=me.subject, summary={})
    db.add(run)
    db.flush()
    job = jobs.create_job(db, kind='http_compare', project_id=project_id, subject_id=run.id, actor=me.subject)
    run.job_id = job.id
    result = {'httpTestRunId': str(run.id), 'jobId': str(job.id), 'status': 'queued'}
    jobs.remember(db, me.subject, idempotency_key, endpoint, payload, 202, result)
    audit.record(db, actor=me.subject, action='http_test.run', entity_type='http_test_run', entity_id=run.id, project_id=project_id,
                 details={'name': name, 'source': body.source, 'workspaceMigration': str(body.workspace_migration_id or ''),
                          'container': body.container, 'dataPackage': str(body.data_package_id or ''), 'execution': str(body.execution_id or ''),
                          'variables': sorted(body.variables),
                          'original': original and original['url'], 'migrated': migrated and migrated['url'], 'cases': len(body.cases)},
                 request=request)
    db.commit()
    jobs.try_publish(db)
    return result


@router.get('/projects/{project_id}/http-tests/runs', response_model=list[HttpTestRunOut])
def list_runs(project_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    _project(db, me, project_id, 'view')
    runs = db.scalars(select(HttpTestRun).where(HttpTestRun.project_id == project_id).order_by(HttpTestRun.created_at.desc()).limit(50))
    return [_out(db, r) for r in runs]


@router.get('/http-tests/runs/{run_id}', response_model=HttpTestRunOut)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    run = db.get(HttpTestRun, run_id)
    if run is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, run.project_id, 'view')
    return _out(db, run)

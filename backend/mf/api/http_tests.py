"""Pruebas: manual requests and automatic comparisons of the original system against the migrated one (docs/19).

Both call systems the user names, so every base URL must be on MF_TEST_ALLOWED_HOSTS (SSRF). A manual request is one short
HTTP call and runs here; an automatic run sends every case to both systems and builds the evidence report, so it is a job."""
import uuid

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, jobs
from ..auth import Principal, authorize, current_principal
from ..config import get_settings
from ..db import get_db
from ..errors import ApiError
from ..models import HttpTestRun, Job, Project
from ..schemas import HttpTestRunIn, HttpTestRunOut, JobOut, ManualTestIn
from ..security import UnsafeInput, validate_test_url
from ..tomcat_war import http_run

router = APIRouter(tags=['pruebas'])


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
    original, migrated = _targets([body.original, body.migrated])
    payload = body.model_dump(mode='json')
    endpoint = f'POST /projects/{project_id}/http-tests/runs'
    replay = jobs.idempotent_replay(db, me.subject, idempotency_key, endpoint, payload)
    if replay:
        return replay
    if db.scalar(select(HttpTestRun.id).where(HttpTestRun.project_id == project_id, HttpTestRun.state.in_(('queued', 'running')))):
        raise ApiError(409, 'INVALID_STATE', 'Ya hay una prueba automática en curso para este proyecto')
    run = HttpTestRun(project_id=project_id, name=body.name, original=original, migrated=migrated, screenshots=body.screenshots,
                      cases=[c.model_dump(mode='json', exclude_none=True) for c in body.cases], created_by=me.subject, summary={})
    db.add(run)
    db.flush()
    job = jobs.create_job(db, kind='http_compare', project_id=project_id, subject_id=run.id, actor=me.subject)
    run.job_id = job.id
    result = {'httpTestRunId': str(run.id), 'jobId': str(job.id), 'status': 'queued'}
    jobs.remember(db, me.subject, idempotency_key, endpoint, payload, 202, result)
    audit.record(db, actor=me.subject, action='http_test.run', entity_type='http_test_run', entity_id=run.id, project_id=project_id,
                 details={'name': body.name, 'original': original['url'], 'migrated': migrated['url'], 'cases': len(body.cases)}, request=request)
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

"""Workspace migration by waves on the platform (18-CORRECCION): every project of a MULTI_PROJECT_REPOSITORY, dependencies
first, a dependent of a project that did not migrate BLOCKED. Long work runs in the worker."""
import uuid

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, jobs
from ..auth import Principal, authorize, current_principal
from ..db import get_db
from ..errors import ApiError
from ..models import Job, Scan, TargetProfile, WorkspaceMigration
from ..schemas import JobOut, WorkspaceMigrationIn, WorkspaceMigrationOut, WorkspaceRetryIn

router = APIRouter(tags=['workspace'])


def _out(db, wm) -> dict:
    d = WorkspaceMigrationOut.model_validate(wm).model_dump(mode='json')
    job = db.get(Job, wm.job_id) if wm.job_id else None
    d['job'] = JobOut.model_validate(job).model_dump(mode='json') if job else None
    return d


@router.post('/scans/{scan_id}/workspace-migrations', status_code=202)
def start(scan_id: uuid.UUID, body: WorkspaceMigrationIn, request: Request, idempotency_key: str | None = Header(None),
          db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, scan.project_id, 'execute')
    authorize(db, me, scan.project_id, 'plan')  # accepting blocking findings for every project is an architect/owner decision
    payload = body.model_dump(mode='json')
    endpoint = f'POST /scans/{scan_id}/workspace-migrations'
    replay = jobs.idempotent_replay(db, me.subject, idempotency_key, endpoint, payload)
    if replay:
        return replay
    ws = (scan.summary or {}).get('workspace') or {}
    if scan.status != 'succeeded' or ws.get('repositoryType') != 'MULTI_PROJECT_REPOSITORY' or ws.get('selectedProject'):
        raise ApiError(409, 'INVALID_STATE', 'Se migra por oleadas desde un análisis de workspace terminado (sin proyecto seleccionado)')
    profile = db.get(TargetProfile, body.profile_id)
    if profile is None or profile.status != 'validated':
        raise ApiError(409, 'PROFILE_NOT_VALIDATED', 'Solo se migra con perfiles validados')
    by_project = ((ws.get('shared') or {}).get('findingsByProject') or {})
    camel2 = sorted(n for n, rules in by_project.items() if 'CAMEL2_VERSION' in rules)
    if camel2 and 'CAMEL2_VERSION' not in body.accept:  # without it no project can pass camel-2-to-3: never run 30 min for nothing
        raise ApiError(409, 'DECISION_REQUIRED', f'{len(camel2)} proyecto(s) usan Camel 2: sin la decisión del arquitecto sobre CAMEL2_VERSION '
                       'ningún proyecto puede pasar de Camel 2 a 3. Indique CAMEL2_VERSION=<motivo de la revisión contra la guía 2→3>.',
                       {'rule': 'CAMEL2_VERSION', 'projects': camel2})
    if db.scalar(select(WorkspaceMigration.id).where(WorkspaceMigration.project_id == scan.project_id,
                                                     WorkspaceMigration.state.in_(('queued', 'running')))):
        raise ApiError(409, 'INVALID_STATE', 'Ya hay una migración de workspace en curso para este proyecto')
    wm = WorkspaceMigration(project_id=scan.project_id, scan_id=scan.id, profile_id=profile.id, java=body.java, accepted=body.accept,
                            approved=sorted(set(body.approve)), created_by=me.subject)
    db.add(wm)
    db.flush()
    job = jobs.create_job(db, kind='workspace_migrate', project_id=scan.project_id, subject_id=wm.id, actor=me.subject)
    wm.job_id = job.id
    result = {'workspaceMigrationId': str(wm.id), 'jobId': str(job.id), 'status': 'queued'}
    jobs.remember(db, me.subject, idempotency_key, endpoint, payload, 202, result)
    audit.record(db, actor=me.subject, action='workspace_migration.request', entity_type='scan', entity_id=scan.id, project_id=scan.project_id,
                 details={'profile': f'{profile.key}@{profile.version}', 'java': body.java, 'accepted': body.accept, 'approved': wm.approved,
                          'projects': len(ws.get('projects', []))}, request=request)
    db.commit()
    jobs.try_publish(db)
    return result


@router.get('/scans/{scan_id}/workspace-migrations', response_model=list[WorkspaceMigrationOut])
def list_for_scan(scan_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, scan.project_id, 'view')
    return [_out(db, w) for w in db.scalars(select(WorkspaceMigration).where(WorkspaceMigration.scan_id == scan_id)
                                             .order_by(WorkspaceMigration.created_at.desc()))]


@router.get('/workspace-migrations/{wm_id}', response_model=WorkspaceMigrationOut)
def get_one(wm_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    wm = db.get(WorkspaceMigration, wm_id)
    if wm is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, wm.project_id, 'view')
    return _out(db, wm)


@router.post('/workspace-migrations/{wm_id}/retry', status_code=202)
def retry(wm_id: uuid.UUID, body: WorkspaceRetryIn, request: Request, idempotency_key: str | None = Header(None),
          db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    """Doc 20 §20: retry some projects (or resume every non-successful one); successful projects are reused, not repeated,
    and dependents blocked only by the retried projects become READY."""
    prev = db.get(WorkspaceMigration, wm_id)
    if prev is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, prev.project_id, 'execute')
    authorize(db, me, prev.project_id, 'plan')
    payload = body.model_dump(mode='json')
    endpoint = f'POST /workspace-migrations/{wm_id}/retry'
    replay = jobs.idempotent_replay(db, me.subject, idempotency_key, endpoint, payload)
    if replay:
        return replay
    if prev.state in ('queued', 'running'):
        raise ApiError(409, 'INVALID_STATE', 'La migración de workspace aún está en curso')
    unknown = sorted(set(body.projects) - set(prev.results or {}))
    if unknown:
        raise ApiError(422, 'VALIDATION_ERROR', 'Proyectos desconocidos en el workspace', {'projects': unknown})
    ok = {n for n, r in (prev.results or {}).items() if r.get('status') in ('SUCCEEDED', 'PARTIAL_SUCCESS')}
    if not body.projects and len(ok) == len(prev.results or {}):
        raise ApiError(409, 'INVALID_STATE', 'Todos los proyectos ya migraron: no hay nada que reanudar')
    if db.scalar(select(WorkspaceMigration.id).where(WorkspaceMigration.project_id == prev.project_id,
                                                     WorkspaceMigration.state.in_(('queued', 'running')))):
        raise ApiError(409, 'INVALID_STATE', 'Ya hay una migración de workspace en curso para este proyecto')
    wm = WorkspaceMigration(project_id=prev.project_id, scan_id=prev.scan_id, profile_id=prev.profile_id, java=prev.java, accepted=prev.accepted,
                            approved=prev.approved, created_by=me.subject, retry_of=prev.id, retry=sorted(set(body.projects)))
    db.add(wm)
    db.flush()
    job = jobs.create_job(db, kind='workspace_migrate', project_id=prev.project_id, subject_id=wm.id, actor=me.subject)
    wm.job_id = job.id
    result = {'workspaceMigrationId': str(wm.id), 'jobId': str(job.id), 'status': 'queued'}
    jobs.remember(db, me.subject, idempotency_key, endpoint, payload, 202, result)
    audit.record(db, actor=me.subject, action='workspace_migration.retry', entity_type='scan', entity_id=prev.scan_id, project_id=prev.project_id,
                 details={'retryOf': str(prev.id), 'projects': wm.retry or 'resume'}, request=request)
    db.commit()
    jobs.try_publish(db)
    return result

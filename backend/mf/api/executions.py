import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, gates, jobs
from ..auth import Principal, authorize, current_principal
from ..db import get_db, session
from ..errors import ApiError
from ..models import Artifact, DiffFile, Execution, Job, JobEvent, Plan, Scan, TargetProfile, Validation, now
from ..planning import evaluate, runnable_recipe_steps
from ..schemas import (ArtifactOut, ExecutionAccepted, LogEvent, Page, PlanOut, DiffFileDetail, DiffFileOut, DiffReviewIn, EvidenceIn, ExecutionCreate, ExecutionOut, JobOut,
                       ValidationOut)
from ..storage import ArtifactStore
from .common import limit_param, paginate
from .scans import plan_out

router = APIRouter(tags=['executions'])


def load_plan(db, me, plan_id, perm='view') -> Plan:
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, plan.project_id, perm)
    return plan


def load_execution(db, me, execution_id, perm='view') -> Execution:
    ex = db.get(Execution, execution_id)
    if ex is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, ex.project_id, perm)
    return ex


def execution_out(db, ex: Execution) -> dict:
    d = ExecutionOut.model_validate(ex).model_dump(mode='json')
    job = db.get(Job, ex.job_id) if ex.job_id else None
    d['job'] = JobOut.model_validate(job).model_dump(mode='json') if job else None
    d['gates'] = gates.evaluate(db, ex)
    d['prBlockers'] = gates.pr_blockers(db, ex)
    return d


@router.get('/plans/{plan_id}', response_model=PlanOut)
def get_plan(plan_id: uuid.UUID, response: Response, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    plan = load_plan(db, me, plan_id)
    response.headers['ETag'] = str(plan.row_version)
    return plan_out(db, plan)


@router.post('/plans/{plan_id}/approve', response_model=PlanOut)
def approve_plan(plan_id: uuid.UUID, request: Request, if_match: str = Header(...), db: Session = Depends(get_db),
                 me: Principal = Depends(current_principal)):
    plan = load_plan(db, me, plan_id, 'plan')
    if if_match.strip('"') != str(plan.row_version):
        raise ApiError(409, 'VERSION_CONFLICT', 'El plan cambió; recargue')
    if plan.status != 'draft':
        raise ApiError(409, 'INVALID_STATE', f'El plan está {plan.status}')
    if db.get(TargetProfile, plan.profile_id).status != 'validated':
        raise ApiError(409, 'PROFILE_NOT_VALIDATED', 'El perfil ya no está validado')
    plan.status, plan.approved_by, plan.approved_at, plan.row_version = 'approved', me.subject, now(), plan.row_version + 1
    audit.record(db, actor=me.subject, action='plan.approve', entity_type='plan', entity_id=plan.id, project_id=plan.project_id,
                 before={'status': 'draft'}, after={'status': 'approved', 'digest': plan.digest}, request=request)
    db.commit()
    return plan_out(db, plan)


@router.post('/plans/{plan_id}/executions', status_code=202, response_model=ExecutionAccepted)
def start_execution(plan_id: uuid.UUID, body: ExecutionCreate, request: Request, idempotency_key: str | None = Header(None),
                    db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    plan = load_plan(db, me, plan_id, 'execute')
    endpoint = f'POST /plans/{plan_id}/executions'
    payload = body.model_dump(mode='json')
    replay = jobs.idempotent_replay(db, me.subject, idempotency_key, endpoint, payload)
    if replay:
        return replay
    if plan.status == 'superseded':
        raise ApiError(409, 'INVALID_STATE', 'El plan fue reemplazado por una versión posterior')
    if body.mode == 'apply' and plan.status != 'approved':
        raise ApiError(409, 'PLAN_NOT_APPROVED', 'La ejecución requiere un plan aprobado')
    if db.get(TargetProfile, plan.profile_id).status != 'validated':
        raise ApiError(409, 'PROFILE_NOT_VALIDATED', 'El preview requiere un perfil validado')
    steps = evaluate(db, plan)
    runnable = runnable_recipe_steps(steps)
    if not runnable:
        raise ApiError(409, 'NOTHING_RUNNABLE', 'Ningún paso automatizable está listo; revise los bloqueos del plan',
                       {'blocked': [{'key': s['key'], 'reasons': s['blockedReasons']} for s in steps if s['state'] == 'blocked']})
    active = db.scalar(select(Execution).where(Execution.plan_id == plan.id, Execution.state.in_(['queued', 'running'])).limit(1))
    if active:
        raise ApiError(409, 'EXECUTION_IN_PROGRESS', 'Ya hay una ejecución en curso para este plan', {'executionId': str(active.id)})
    scan = db.get(Scan, plan.scan_id)
    if not scan.snapshot_hash:
        raise ApiError(409, 'SNAPSHOT_MISSING', 'El análisis no tiene snapshot; repita el análisis')
    ex = Execution(plan_id=plan.id, project_id=plan.project_id, mode=body.mode, base_sha=scan.commit_sha, base_snapshot=scan.snapshot_hash,
                   created_by=me.subject,
                   steps=[{'key': s['key'], 'title': s['title'], 'kind': s['kind'],
                           'state': 'pending' if s['state'] == 'ready' else ('skipped' if s['kind'] == 'recipe' else s['state']),
                           'reasons': s['blockedReasons']} for s in steps if s['kind'] == 'recipe'])
    db.add(ex)
    db.flush()
    job = jobs.create_job(db, kind=body.mode, project_id=plan.project_id, subject_id=ex.id, actor=me.subject)
    ex.job_id = job.id
    result = {'executionId': str(ex.id), 'jobId': str(job.id), 'status': 'queued'}
    jobs.remember(db, me.subject, idempotency_key, endpoint, payload, 202, result)
    audit.record(db, actor=me.subject, action=f'execution.{body.mode}', entity_type='execution', entity_id=ex.id,
                 project_id=plan.project_id, details={'plan': str(plan.id), 'steps': [s['key'] for s in runnable]}, request=request)
    db.commit()
    jobs.try_publish(db)
    return result


@router.post('/plans/{plan_id}/preview', status_code=202, response_model=ExecutionAccepted)
def preview(plan_id: uuid.UUID, request: Request, idempotency_key: str | None = Header(None), db: Session = Depends(get_db),
            me: Principal = Depends(current_principal)):
    return start_execution(plan_id, ExecutionCreate(mode='preview'), request, idempotency_key, db, me)


@router.get('/plans/{plan_id}/executions', response_model=list[ExecutionOut])
def list_executions(plan_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    load_plan(db, me, plan_id)
    return [ExecutionOut.model_validate(e).model_dump(mode='json') for e in
            db.scalars(select(Execution).where(Execution.plan_id == plan_id).order_by(Execution.created_at.desc()))]


@router.get('/projects/{project_id}/executions', response_model=Page[ExecutionOut])
def project_executions(project_id: uuid.UUID, limit: int = limit_param(20), cursor: str | None = None,
                       db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    authorize(db, me, project_id, 'view')
    stmt = select(Execution).where(Execution.project_id == project_id).order_by(Execution.created_at.desc(), Execution.id)
    return paginate(db, stmt, limit, cursor, lambda e: ExecutionOut.model_validate(e).model_dump(mode='json'))


@router.get('/executions/{execution_id}', response_model=ExecutionOut)
def get_execution(execution_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    return execution_out(db, load_execution(db, me, execution_id))


@router.post('/executions/{execution_id}/cancel', status_code=202, response_model=ExecutionOut)
def cancel_execution(execution_id: uuid.UUID, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    ex = load_execution(db, me, execution_id, 'execute')
    job = db.get(Job, ex.job_id)
    jobs.request_cancel(db, job, me.subject)
    if job.state == 'cancelled':
        ex.state = 'cancelled'
    audit.record(db, actor=me.subject, action='execution.cancel', entity_type='execution', entity_id=ex.id, project_id=ex.project_id, request=request)
    db.commit()
    return execution_out(db, ex)


@router.post('/executions/{execution_id}/rollback', response_model=ExecutionOut)
def rollback(execution_id: uuid.UUID, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    """Discard the candidate. The source repository was never modified; artifacts are kept as evidence."""
    ex = load_execution(db, me, execution_id, 'execute')
    if ex.state in ('queued', 'running'):
        raise ApiError(409, 'INVALID_STATE', 'Cancele la ejecución antes de descartarla')
    if ex.rolled_back:
        raise ApiError(409, 'INVALID_STATE', 'El candidato ya fue descartado')
    if ex.pr_url:
        raise ApiError(409, 'INVALID_STATE', 'Ya existe un PR; ciérrelo en el proveedor Git')
    ex.rolled_back, ex.state = True, 'rolled_back'
    audit.record(db, actor=me.subject, action='execution.rollback', entity_type='execution', entity_id=ex.id, project_id=ex.project_id,
                 details={'candidateSha': ex.candidate_sha}, request=request)
    db.commit()
    return execution_out(db, ex)


@router.get('/executions/{execution_id}/diff', response_model=Page[DiffFileOut])
def diff_files(execution_id: uuid.UUID, limit: int = limit_param(100), cursor: str | None = None,
               db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    load_execution(db, me, execution_id)
    stmt = select(DiffFile).where(DiffFile.execution_id == execution_id).order_by(DiffFile.path, DiffFile.id)
    return paginate(db, stmt, limit, cursor, lambda f: DiffFileOut.model_validate(f).model_dump(mode='json'))


def load_diff_file(db, me, file_id, perm='view') -> tuple[DiffFile, Execution]:
    f = db.get(DiffFile, file_id)
    if f is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    return f, load_execution(db, me, f.execution_id, perm)


@router.get('/diff-files/{file_id}', response_model=DiffFileDetail)
def diff_file(file_id: uuid.UUID, response: Response, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    f, _ = load_diff_file(db, me, file_id)
    response.headers['ETag'] = str(f.version)
    return f


@router.post('/diff-files/{file_id}/review', response_model=DiffFileOut)
def review_diff_file(file_id: uuid.UUID, body: DiffReviewIn, request: Request, if_match: str = Header(...),
                     db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    f, ex = load_diff_file(db, me, file_id, 'approve_diff')
    project = authorize(db, me, ex.project_id, 'approve_diff')
    if if_match.strip('"') != str(f.version):
        raise ApiError(409, 'VERSION_CONFLICT', 'El archivo cambió; recargue')
    if ex.mode != 'apply' or ex.state not in ('succeeded', 'partial_success', 'failed') or ex.rolled_back:
        raise ApiError(409, 'INVALID_STATE', 'Solo se revisan diffs de ejecuciones aplicadas y vigentes')
    if project.independent_review and ex.created_by == me.subject:
        raise ApiError(403, 'INDEPENDENT_REVIEW_REQUIRED', 'Quien lanzó la ejecución no puede aprobar su propio diff')
    if body.decision == 'rejected' and len(body.reason.strip()) < 10:
        raise ApiError(422, 'VALIDATION_ERROR', 'Rechazar exige un motivo (mínimo 10 caracteres)')
    before = f.review_status
    f.review_status, f.reviewed_by, f.review_reason, f.version = body.decision, me.subject, body.reason or None, f.version + 1
    if body.minutes:
        from ..models import EffortLog
        db.add(EffortLog(project_id=ex.project_id, category='diffReview', minutes=body.minutes, units=1, subject_type='diff_file',
                         subject_id=str(f.id), note=f.path, actor=me.subject))
    audit.record(db, actor=me.subject, action='diff.review', entity_type='diff_file', entity_id=f.id, project_id=ex.project_id,
                 before={'status': before}, after={'status': f.review_status}, details={'path': f.path, 'reason': body.reason[:500]}, request=request)
    db.commit()
    return f


@router.get('/executions/{execution_id}/validations', response_model=list[ValidationOut])
def validations(execution_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    load_execution(db, me, execution_id)
    return list(db.scalars(select(Validation).where(Validation.execution_id == execution_id).order_by(Validation.created_at)))


@router.post('/executions/{execution_id}/validations', response_model=ValidationOut, status_code=201)
def record_evidence(execution_id: uuid.UUID, body: EvidenceIn, request: Request, db: Session = Depends(get_db),
                    me: Principal = Depends(current_principal)):
    """Manual evidence for suites run outside the tool (equivalence/contracts). Automatic suites run in the worker."""
    ex = load_execution(db, me, execution_id, 'evidence')
    project = authorize(db, me, ex.project_id, 'evidence')
    if ex.mode != 'apply' or ex.state not in ('succeeded', 'partial_success', 'failed') or ex.rolled_back:
        raise ApiError(409, 'INVALID_STATE', 'La evidencia se registra sobre una ejecución aplicada y vigente')
    if project.independent_review and ex.created_by == me.subject:
        raise ApiError(403, 'INDEPENDENT_REVIEW_REQUIRED', 'La evidencia debe registrarla alguien distinto de quien ejecutó')
    v = Validation(execution_id=ex.id, suite=body.suite, target='candidate', gate='G4' if body.suite == 'equivalence' else 'G3',
                   status=body.status, detail=body.detail, recorded_by=me.subject)
    db.add(v)
    audit.record(db, actor=me.subject, action='validation.evidence', entity_type='execution', entity_id=ex.id, project_id=ex.project_id,
                 details={'suite': body.suite, 'status': body.status}, request=request)
    db.commit()
    return v


@router.post('/executions/{execution_id}/pull-request', status_code=202, response_model=ExecutionAccepted)
def pull_request(execution_id: uuid.UUID, request: Request, idempotency_key: str | None = Header(None),
                 db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    ex = load_execution(db, me, execution_id, 'pull_request')
    endpoint = f'POST /executions/{execution_id}/pull-request'
    replay = jobs.idempotent_replay(db, me.subject, idempotency_key, endpoint, {})
    if replay:
        return replay
    if ex.pr_url:
        raise ApiError(409, 'INVALID_STATE', 'El PR ya existe', {'url': ex.pr_url})
    blockers = gates.pr_blockers(db, ex)
    if blockers:
        raise ApiError(409, 'GATES_NOT_MET', 'No se cumplen las condiciones para un PR borrador', {'reasons': blockers})
    if db.scalar(select(Job).where(Job.subject_id == ex.id, Job.kind == 'pull_request', Job.state.in_(['queued', 'running']))):
        raise ApiError(409, 'INVALID_STATE', 'Ya hay una creación de PR en curso')
    job = jobs.create_job(db, kind='pull_request', project_id=ex.project_id, subject_id=ex.id, actor=me.subject)
    result = {'executionId': str(ex.id), 'jobId': str(job.id), 'status': 'queued'}
    jobs.remember(db, me.subject, idempotency_key, endpoint, {}, 202, result)
    audit.record(db, actor=me.subject, action='pull_request.request', entity_type='execution', entity_id=ex.id, project_id=ex.project_id, request=request)
    db.commit()
    jobs.try_publish(db)
    return result


# --- Jobs, events, artifacts -------------------------------------------------------------------
def load_job(db, me, job_id, perm='view') -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, job.project_id, perm)
    return job


@router.get('/jobs/{job_id}', response_model=JobOut)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    return load_job(db, me, job_id)


@router.post('/jobs/{job_id}/cancel', response_model=JobOut, status_code=202)
def cancel_job(job_id: uuid.UUID, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    job = load_job(db, me, job_id)
    authorize(db, me, job.project_id, 'scan' if job.kind == 'scan' else ('pull_request' if job.kind == 'pull_request' else 'execute'))  # modernize: execute
    jobs.request_cancel(db, job, me.subject)
    if job.state == 'cancelled':
        _mark_subject_cancelled(db, job)
    audit.record(db, actor=me.subject, action='job.cancel', entity_type='job', entity_id=job.id, project_id=job.project_id, request=request)
    db.commit()
    return job


def _mark_subject_cancelled(db, job):
    if job.kind == 'scan':
        s = db.get(Scan, job.subject_id)
        if s and s.status == 'queued':
            s.status = 'cancelled'
    elif job.kind in ('preview', 'apply'):
        e = db.get(Execution, job.subject_id)
        if e and e.state == 'queued':
            e.state = 'cancelled'
    elif job.kind in ('modernize', 'workspace_migrate'):
        from ..models import Modernization, WorkspaceMigration
        m = db.get(Modernization if job.kind == 'modernize' else WorkspaceMigration, job.subject_id)
        if m and m.state == 'queued':
            m.state = 'cancelled'


@router.get('/jobs/{job_id}/logs', response_model=list[LogEvent])
def job_logs(job_id: uuid.UUID, after: int = 0, limit: int = limit_param(200), db: Session = Depends(get_db),
             me: Principal = Depends(current_principal)):
    load_job(db, me, job_id)
    rows = db.scalars(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.id > after).order_by(JobEvent.id).limit(limit))
    return [{'id': e.id, 'at': e.at.isoformat(), 'level': e.level, 'phase': e.phase, 'message': e.message} for e in rows]


@router.get('/jobs/{job_id}/events')
async def job_events(job_id: uuid.UUID, request: Request, last_event_id: int | None = Header(None),
                     db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    """SSE stream of redacted job events; resumable with Last-Event-ID. Ends when the job is terminal."""
    load_job(db, me, job_id)
    start = last_event_id or 0

    async def stream():
        cursor, idle = start, 0
        while not await request.is_disconnected():
            with session() as s:
                rows = list(s.scalars(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.id > cursor).order_by(JobEvent.id).limit(200)))
                job = s.get(Job, job_id)
            for e in rows:
                cursor = e.id
                data = json.dumps({'level': e.level, 'phase': e.phase, 'message': e.message, 'at': e.at.isoformat(),
                                   'state': job.state, 'progress': job.progress})
                yield f'id: {e.id}\nevent: log\ndata: {data}\n\n'
            if job.state in jobs.TERMINAL and not rows:
                yield f"event: end\ndata: {json.dumps({'state': job.state})}\n\n"
                return
            idle = 0 if rows else idle + 1
            if idle and idle % 15 == 0:
                yield ': keep-alive\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@router.get('/projects/{project_id}/artifacts', response_model=Page[ArtifactOut])
def project_artifacts(project_id: uuid.UUID, scan_id: uuid.UUID | None = Query(None, alias='scanId'),
                      execution_id: uuid.UUID | None = Query(None, alias='executionId'),
                      limit: int = limit_param(), cursor: str | None = None, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    authorize(db, me, project_id, 'view')
    stmt = select(Artifact).where(Artifact.project_id == project_id)
    if scan_id:
        stmt = stmt.where(Artifact.scan_id == scan_id)
    if execution_id:
        stmt = stmt.where(Artifact.execution_id == execution_id)
    stmt = stmt.order_by(Artifact.created_at.desc(), Artifact.id)
    return paginate(db, stmt, limit, cursor, lambda a: ArtifactOut.model_validate(a).model_dump(mode='json'))


@router.get('/artifacts/{artifact_id}/download')
def download(artifact_id: uuid.UUID, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    art = db.get(Artifact, artifact_id)
    if art is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, art.project_id, 'view')
    try:
        data = ArtifactStore().read(art)
    except (OSError, ValueError):
        raise ApiError(410, 'ARTIFACT_UNAVAILABLE', 'Artefacto no disponible o íntegro') from None
    audit.record(db, actor=me.subject, action='artifact.download', entity_type='artifact', entity_id=art.id, project_id=art.project_id,
                 details={'name': art.name, 'sha256': art.sha256}, request=request)
    db.commit()
    # HTML reports are served as attachments so they never render in the API origin.
    return Response(data, media_type=art.content_type, headers={
        'Content-Disposition': f'attachment; filename="{art.name}"', 'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'private, no-store', 'X-Checksum-SHA256': art.sha256})

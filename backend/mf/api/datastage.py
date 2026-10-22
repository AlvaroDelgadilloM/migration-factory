"""DataStage -> Airflow migrations of a DATASTAGE_AIRFLOW project (migration-factory-datastage-airflow 63). The work runs in the
worker (job kind datastage_migrate): parsing, generation and the DagBag gate never run inside an HTTP request."""
import uuid

from fastapi import APIRouter, Body, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, jobs
from ..auth import Principal, authorize, current_principal
from ..datastage import reconcile
from ..db import get_db
from ..errors import ApiError
from ..models import DataStageMigration, Job, now
from ..schemas import DataStageActionIn, DataStageMigrationIn, DataStageMigrationOut, DataStageSchedulesIn, JobOut

router = APIRouter(tags=['datastage'])


def _out(db, dm) -> dict:
    d = DataStageMigrationOut.model_validate(dm).model_dump(mode='json')
    job = db.get(Job, dm.job_id) if dm.job_id else None
    d['job'] = JobOut.model_validate(job).model_dump(mode='json') if job else None
    return d


@router.post('/projects/{project_id}/datastage-migrations', status_code=202)
def start(project_id: uuid.UUID, body: DataStageMigrationIn, request: Request, idempotency_key: str | None = Header(None),
          db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    p = authorize(db, me, project_id, 'execute')
    if p.migration_type != 'DATASTAGE_AIRFLOW':
        raise ApiError(409, 'WRONG_MIGRATION_TYPE', 'El proyecto es Java/Camel: use análisis y planes')
    payload = body.model_dump(mode='json')
    endpoint = f'POST /projects/{project_id}/datastage-migrations'
    replay = jobs.idempotent_replay(db, me.subject, idempotency_key, endpoint, payload)
    if replay:
        return replay
    if db.scalar(select(DataStageMigration.id).where(DataStageMigration.project_id == p.id,
                                                     DataStageMigration.state.in_(('queued', 'running')))):
        raise ApiError(409, 'INVALID_STATE', 'Ya hay una migración DataStage en curso para este proyecto')
    prev = db.scalar(select(DataStageMigration).where(DataStageMigration.project_id == p.id)
                     .order_by(DataStageMigration.created_at.desc()).limit(1))
    # the worklist progress follows the project: same job|stage|text = same key, so a re-run keeps what was done
    if payload.get('schedules') is None:  # the schedules saved on the project's last run carry over, like the worklist
        payload['schedules'] = dict((prev.options or {}).get('schedules') or {}) if prev else {}
    dm = DataStageMigration(project_id=p.id, options=payload, created_by=me.subject, action_state=dict(prev.action_state) if prev else {})
    db.add(dm)
    db.flush()
    job = jobs.create_job(db, kind='datastage_migrate', project_id=p.id, subject_id=dm.id, actor=me.subject)
    dm.job_id = job.id
    result = {'dataStageMigrationId': str(dm.id), 'jobId': str(job.id), 'status': 'queued'}
    jobs.remember(db, me.subject, idempotency_key, endpoint, payload, 202, result)
    audit.record(db, actor=me.subject, action='datastage_migration.request', entity_type='project', entity_id=p.id, project_id=p.id,
                 details={'migrationType': 'DATASTAGE_AIRFLOW'} | payload, request=request)
    db.commit()
    jobs.try_publish(db)
    return result


@router.get('/projects/{project_id}/datastage-migrations', response_model=list[DataStageMigrationOut])
def list_for_project(project_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    authorize(db, me, project_id, 'view')
    return [_out(db, d) for d in db.scalars(select(DataStageMigration).where(DataStageMigration.project_id == project_id)
                                             .order_by(DataStageMigration.created_at.desc()).limit(20))]


@router.get('/datastage-migrations/{dm_id}', response_model=DataStageMigrationOut)
def get_one(dm_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    dm = db.get(DataStageMigration, dm_id)
    if dm is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, dm.project_id, 'view')
    return _out(db, dm)


@router.patch('/datastage-migrations/{dm_id}/actions', response_model=DataStageMigrationOut)
def set_action(dm_id: uuid.UUID, body: DataStageActionIn, request: Request, db: Session = Depends(get_db),
               me: Principal = Depends(current_principal)):
    """Mark one manual action of the worklist done / pending (who and when are recorded; audited)."""
    dm = db.get(DataStageMigration, dm_id)
    if dm is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, dm.project_id, 'execute')
    if body.key not in {a['key'] for a in (dm.summary or {}).get('actions', [])}:
        raise ApiError(404, 'NOT_FOUND', 'Acción inexistente en esta migración')
    state = dict(dm.action_state or {})
    state[body.key] = {'done': body.done, 'by': me.subject, 'at': now().isoformat(timespec='seconds'), 'note': body.note}
    dm.action_state = state
    audit.record(db, actor=me.subject, action='datastage_action.' + ('done' if body.done else 'reopen'), entity_type='datastage_migration',
                 entity_id=dm.id, project_id=dm.project_id, details={'key': body.key, 'note': body.note}, request=request)
    db.commit()
    return _out(db, dm)


@router.put('/datastage-migrations/{dm_id}/schedules', response_model=DataStageMigrationOut)
def set_schedules(dm_id: uuid.UUID, body: DataStageSchedulesIn, request: Request, db: Session = Depends(get_db),
                  me: Principal = Depends(current_principal)):
    """Cron of each DAG (the DSX has no schedule): saved on this run and applied by the next migration (audited)."""
    dm = db.get(DataStageMigration, dm_id)
    if dm is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, dm.project_id, 'execute')
    unknown = {n for n in body.schedules if not (dm.results or {}).get(n, {}).get('ownDag')}
    if unknown:
        raise ApiError(422, 'VALIDATION', 'Sin DAG propio en esta migración (no se pueden programar): ' + ', '.join(sorted(unknown)[:5]))
    dm.options = dict(dm.options or {}) | {'schedules': body.schedules}
    audit.record(db, actor=me.subject, action='datastage_migration.schedules', entity_type='datastage_migration', entity_id=dm.id,
                 project_id=dm.project_id, details={'schedules': body.schedules}, request=request)
    db.commit()
    return _out(db, dm)


MAX_RESULT_BYTES = 5_000_000


@router.post('/datastage-migrations/{dm_id}/reconciliations', response_model=DataStageMigrationOut)
def upload_reconciliation(dm_id: uuid.UUID, request: Request, result: dict = Body(...), db: Session = Depends(get_db),
                          me: Principal = Depends(current_principal)):
    """Evidence of the reconciliation run in the client's environment (tests/reconciliation/run_reconciliation.py of the
    generated project): evaluated here, kept with the migration and audited. Nothing connects to the client's databases."""
    dm = db.get(DataStageMigration, dm_id)
    if dm is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, dm.project_id, 'execute')
    if int(request.headers.get('content-length') or 0) > MAX_RESULT_BYTES:
        raise ApiError(413, 'PAYLOAD_TOO_LARGE', 'El resultado supera 5 MB')
    if dm.state not in ('succeeded', 'partial_success'):
        raise ApiError(409, 'INVALID_STATE', 'La migración no terminó: no hay nada que reconciliar todavía')
    try:
        ev = reconcile.evaluate(result, set((dm.results or {}).keys()))
    except reconcile.InvalidResult as e:
        raise ApiError(422, 'RECONCILIATION_INVALID', str(e)) from None
    at = now().isoformat(timespec='seconds')
    ev |= {'uploadedAt': at, 'uploadedBy': me.subject}
    history = [{'at': at, 'by': me.subject, 'status': ev['status'], 'counts': ev['counts']}] + list((dm.reconciliation or {}).get('history', []))
    dm.reconciliation = {'latest': ev, 'history': history[:20]}
    audit.record(db, actor=me.subject, action='datastage_reconciliation.upload', entity_type='datastage_migration', entity_id=dm.id,
                 project_id=dm.project_id, details={'status': ev['status'], 'counts': ev['counts'], 'legacy': ev['legacy'],
                                                    'target': ev['target']}, request=request)
    db.commit()
    return _out(db, dm)

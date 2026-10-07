"""Job creation with transactional outbox and Idempotency-Key handling. No work runs in the HTTP process."""
import hashlib
import uuid
import json
import logging

from sqlalchemy import select, update

from .config import get_settings
from .errors import ApiError
from .models import IdempotencyRecord, Job, JobEvent, Outbox, now

log = logging.getLogger('mf.jobs')
QUEUE = 'mf-jobs'
LONG_QUEUE = 'mf-long'  # workspace migrations and modernizations: own worker, never delay scans/executions
LONG_KINDS = {'workspace_migrate', 'modernize'}


def queue_for(kind: str | None) -> str:
    return LONG_QUEUE if kind in LONG_KINDS else QUEUE
TERMINAL = {'succeeded', 'partial_success', 'failed', 'blocked', 'cancelled', 'timed_out'}


def request_hash(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def idempotent_replay(db, actor, key, endpoint, payload):
    """Return a stored response for a repeated key, 409 if the key was used with another payload."""
    if not key:
        return None
    if len(key) > 200:
        raise ApiError(422, 'VALIDATION_ERROR', 'Idempotency-Key demasiado largo')
    rec = db.get(IdempotencyRecord, (actor, key))
    if rec is None:
        return None
    if rec.endpoint != endpoint or rec.request_hash != request_hash(payload):
        raise ApiError(409, 'IDEMPOTENCY_CONFLICT', 'Idempotency-Key reutilizado con un payload distinto')
    return rec.response_body


def remember(db, actor, key, endpoint, payload, status, body):
    if key:
        db.add(IdempotencyRecord(actor=actor, key=key, endpoint=endpoint, request_hash=request_hash(payload),
                                 response_status=status, response_body=body))


def create_job(db, *, kind, project_id, subject_id, actor) -> Job:
    job = Job(kind=kind, project_id=project_id, subject_id=subject_id, created_by=actor,
              max_attempts=get_settings().max_attempts)
    db.add(job)
    db.flush()
    db.add(Outbox(event_type='job.enqueue', payload={'jobId': str(job.id)}))
    db.add(JobEvent(job_id=job.id, phase='queued', message=f'Trabajo {kind} en cola'))
    return job


def _redis():
    from redis import Redis
    return Redis.from_url(get_settings().redis_url)


def publish_outbox(db, limit=50) -> int:
    """Move committed outbox rows to the RQ queue. Safe to run concurrently (row locks) and repeatedly."""
    from rq import Queue
    queues = {}
    stmt = select(Outbox).where(Outbox.published_at.is_(None)).order_by(Outbox.id).limit(limit)
    if db.bind.dialect.name == 'postgresql':
        stmt = stmt.with_for_update(skip_locked=True)
    rows = list(db.scalars(stmt))
    for row in rows:
        row.attempts += 1
        job_id = row.payload['jobId']
        job = db.get(Job, uuid.UUID(str(job_id)))
        limit = get_settings().workspace_timeout_seconds if job is not None and job.kind == 'workspace_migrate' else get_settings().job_timeout_seconds
        # RQ job id = our job id + attempt counter; the worker claims through the DB lease, so a
        # duplicate delivery is harmless (at-least-once delivery, idempotent claim).
        name = queue_for(job.kind if job is not None else None)
        q = queues.setdefault(name, Queue(name, connection=_redis()))
        q.enqueue('mf.worker.tasks.run_job', job_id, job_id=f'{job_id}-{row.id}', job_timeout=limit + 120,
                  result_ttl=3600, failure_ttl=86400)
        row.published_at = now()
    db.commit()
    return len(rows)


def try_publish(db):
    """Best-effort immediate publish after commit; the worker dispatcher retries anything left."""
    try:
        publish_outbox(db)
    except Exception as e:  # Redis down: rows stay in the outbox
        db.rollback()
        log.warning('outbox publish deferred: %s', e)


def request_cancel(db, job: Job, actor) -> Job:
    if job.state in TERMINAL:
        raise ApiError(409, 'INVALID_STATE', f'El trabajo ya terminó ({job.state})')
    if job.state == 'queued':
        # Not yet claimed: the claim query only takes queued jobs, so this is final.
        res = db.execute(update(Job).where(Job.id == job.id, Job.state == 'queued')
                         .values(state='cancelled', cancel_requested=True, finished_at=now()))
        if res.rowcount:
            db.add(JobEvent(job_id=job.id, phase='cancelled', level='warn', message=f'Cancelado en cola por {actor}'))
            return job
    job.cancel_requested = True
    db.add(JobEvent(job_id=job.id, phase=job.phase, level='warn', message=f'Cancelación solicitada por {actor}; esperando confirmación del worker'))
    return job

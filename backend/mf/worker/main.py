"""Worker process: RQ consumer + dispatcher (outbox relay and lease recovery).

Run: python -m mf.worker.main
"""
import ctypes
import logging
import os
import threading
import time
import uuid
from datetime import timedelta

from redis import Redis
from rq import Queue, Worker
from sqlalchemy import select

from ..config import get_settings
from ..db import session
from ..jobs import QUEUE, publish_outbox
from ..models import Execution, Job, JobEvent, Outbox, Scan, now
from . import tasks

log = logging.getLogger('mf.worker')
SECRET_ENV = ('MF_DATABASE_URL', 'MF_REDIS_URL', 'MF_DEV_JWT_SECRET')


def harden_process():
    """Keep secrets away from build subprocesses running untrusted Maven plugins (same uid):
    - credential references (env:NAME) are captured and removed from os.environ
    - the process is made non-dumpable so /proc/<pid>/environ and memory are not readable by children
    """
    get_settings()  # read settings before scrubbing
    tasks.CREDENTIALS.update({k: v for k, v in os.environ.items() if k.startswith('MF_CRED_')})
    for k in list(os.environ):
        if k.startswith('MF_CRED_') or k in SECRET_ENV:
            os.environ.pop(k, None)
    try:
        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 4
    except (OSError, AttributeError):
        log.warning('prctl not available (non-Linux): process remains dumpable')


def recover_leases():
    """Expired lease => requeue (if attempts remain) or fail. Cancel-requested => cancelled."""
    with session() as db:
        stale = list(db.scalars(select(Job).where(Job.state == 'running', Job.lease_until < now())))
        for job in stale:
            if job.cancel_requested:
                job.state, msg = 'cancelled', 'Lease expirado con cancelación solicitada'
            elif job.attempts < job.max_attempts:
                job.state, job.lease_owner, job.lease_until = 'queued', None, None
                db.add(Outbox(event_type='job.enqueue', payload={'jobId': str(job.id)}))
                msg = 'Lease expirado (worker caído): se reencola'
            else:
                job.state, job.error_code, msg = 'failed', 'LEASE_EXPIRED', 'Lease expirado sin reintentos restantes'
            if job.state in ('failed', 'cancelled'):
                job.finished_at = now()
                if job.kind == 'scan':
                    db.get(Scan, job.subject_id).status = job.state
                elif job.kind in ('preview', 'apply'):
                    db.get(Execution, job.subject_id).state = job.state
            db.add(JobEvent(job_id=job.id, level='warn', phase='recovery', message=msg))
        db.commit()
        return len(stale)


def dispatcher(stop: threading.Event, interval=3):
    while not stop.wait(interval):
        try:
            recover_leases()
            with session() as db:
                publish_outbox(db)
        except Exception:
            log.exception('dispatcher iteration failed')


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    s = get_settings()
    redis = Redis.from_url(s.redis_url)
    harden_process()
    s.work_dir.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    threading.Thread(target=dispatcher, args=(stop,), daemon=True).start()
    # SimpleWorker: jobs run in this process so the heartbeat/cancel logic owns the subprocess group.
    from rq.worker import SimpleWorker
    # MF_WORKER_QUEUES: which queues this worker consumes (default: short jobs). Long jobs (workspace migrations,
    # modernizations) go to 'mf-long' so an analysis never waits behind a 30-minute workspace run.
    names = [q.strip() for q in os.environ.get('MF_WORKER_QUEUES', QUEUE).split(',') if q.strip()]
    log.info('consumiendo colas: %s', names)
    SimpleWorker([Queue(n, connection=redis) for n in names], connection=redis, name=f'mf-{uuid.uuid4().hex[:8]}').work(with_scheduler=False)
    stop.set()


if __name__ == '__main__':
    main()

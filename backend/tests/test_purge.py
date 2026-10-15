"""Deleting what the platform generates: an execution, a whole project, and the automatic retention. The audit trail stays."""
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from helpers import FakeMaven, install, run_job, scanned_project
from mf import purge
from mf.config import get_settings
from mf.db import session
from mf.models import Artifact, AuditEvent, now
from test_worker import _ready_plan

API = '/api/v1'


def _applied(client, token, pilot_repo, monkeypatch):
    pid, scan = scanned_project(client, token, pilot_repo, monkeypatch)
    plan = _ready_plan(client, token, scan)
    install(monkeypatch, FakeMaven())
    r = client.post(f"{API}/plans/{plan['id']}/executions", headers=token('diego.dev'), json={'mode': 'apply'}).json()
    run_job(r['jobId'])
    return pid, scan, r['executionId']


def _files(**where):
    base = Path(get_settings().artifact_dir)
    where = {k: uuid.UUID(str(v)) for k, v in where.items()}
    with session() as db:
        rows = list(db.scalars(select(Artifact).filter_by(**where)))
        return [base / a.object_key for a in rows]


def test_delete_an_execution_and_its_files(client, token, pilot_repo, monkeypatch):
    pid, _, eid = _applied(client, token, pilot_repo, monkeypatch)
    files = _files(execution_id=eid)
    assert files and all(f.exists() for f in files)
    assert client.delete(f'{API}/executions/{eid}', headers=token('diego.dev')).status_code == 403          # developer cannot
    assert client.delete(f'{API}/executions/{eid}', headers=token('ana.arquitecta')).status_code == 204
    assert client.get(f'{API}/executions/{eid}', headers=token('ana.arquitecta')).status_code == 404
    assert not any(f.exists() for f in files) and not _files(execution_id=eid)
    events = client.get(f'{API}/audit', headers=token('aldo.auditor'), params={'projectId': pid}).json()['items']
    ev = next(e for e in events if e['action'] == 'execution.delete')
    assert ev['details']['files'] == len(files)


def test_delete_a_whole_project_keeps_the_audit(client, token, pilot_repo, monkeypatch):
    pid, scan, eid = _applied(client, token, pilot_repo, monkeypatch)
    files = _files(project_id=pid)
    name = client.get(f'{API}/projects/{pid}', headers=token('ana.arquitecta')).json()['name']
    url = f'{API}/projects/{pid}'
    assert client.delete(url, headers=token('diego.dev'), params={'confirm': name}).status_code == 403
    assert client.delete(url, headers=token('ana.arquitecta'), params={'confirm': 'otro'}).status_code == 422
    assert client.delete(url, headers=token('ana.arquitecta'), params={'confirm': name}).status_code == 204
    assert client.get(url, headers=token('ana.arquitecta')).status_code == 404
    assert client.get(f"{API}/scans/{scan['id']}", headers=token('ana.arquitecta')).status_code == 404
    assert not any(f.exists() for f in files) and not _files(project_id=pid)
    with session() as db:  # the audit trail survives the project
        actions = set(db.scalars(select(AuditEvent.action).where(AuditEvent.project_id == uuid.UUID(pid))))
    assert {'project.delete', 'project.create'} <= actions


def test_retention_deletes_only_old_heavy_files(client, token, pilot_repo, monkeypatch):
    pid, scan, eid = _applied(client, token, pilot_repo, monkeypatch)
    old = now() - timedelta(days=40)
    with session() as db:
        arts = list(db.scalars(select(Artifact).where(Artifact.project_id == uuid.UUID(pid))))
        for a in arts:
            a.created_at = old
        kinds = {a.kind for a in arts}
        db.commit()
    assert {'candidate-zip', 'patch', 'snapshot', 'report-json'} <= kinds
    with session() as db:
        assert purge.purge_expired(db, days=0)['files'] == 0                     # 0 = off
        stats = purge.purge_expired(db, days=30)
        db.commit()
    left = {a for a in _files(project_id=pid)}
    with session() as db:
        kinds_left = {a.kind for a in db.scalars(select(Artifact).where(Artifact.project_id == uuid.UUID(pid)))}
    assert stats['files'] > 0 and not kinds_left & {'candidate-zip', 'patch'}
    assert 'report-json' in kinds_left and 'snapshot' in kinds_left            # reports kept; the latest scan keeps its snapshot
    assert all(f.exists() for f in left)
    ex = client.get(f'{API}/executions/{eid}', headers=token('diego.dev'))
    assert ex.status_code == 200                                               # the execution is still readable

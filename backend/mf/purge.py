"""Deleting what the platform generates: an execution, a whole project, and the automatic retention of heavy files.

Files live in the artifact store (volume `artifacts`), records in the database. Deleting removes both; the audit trail is
never deleted (AuditEvent has no foreign key to the project), so who deleted what and when stays recorded.

Retention (MF_ARTIFACT_RETENTION_DAYS, 0 = off) only removes the heavy, re-creatable files older than N days: delivered
candidates (zip/war), candidate patches and the snapshots of scans that are not the latest one of their project. Reports,
logs and evidence are kept, so a past execution can still be read and audited."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select

from .config import get_settings
from .models import Artifact, Execution, Job, Project, Repository, Scan, Upload, now
from .security import safe_join
from .storage import ArtifactStore

ACTIVE = ('queued', 'running')
HEAVY_KINDS = ('candidate-zip', 'modernized-zip', 'patch')


class Busy(Exception):
    pass


def _drop(db, arts) -> dict:
    store, n, size = ArtifactStore(), 0, 0
    for a in arts:
        store.delete(a)
        size += a.size_bytes or 0
        n += 1
        db.delete(a)
    return {'files': n, 'bytes': size}


def delete_execution(db, ex: Execution) -> dict:
    if ex.state in ACTIVE:
        raise Busy('La ejecución sigue en curso: cancélela antes de eliminarla')
    stats = _drop(db, db.scalars(select(Artifact).where(Artifact.execution_id == ex.id)))
    db.delete(ex)  # diff files, validations and modernizations go with it (ON DELETE CASCADE)
    return stats


def delete_project(db, p: Project) -> dict:
    if db.scalar(select(Job.id).where(Job.project_id == p.id, Job.state.in_(ACTIVE)).limit(1)):
        raise Busy('El proyecto tiene trabajos en curso: espere o cancélelos antes de eliminarlo')
    stats = _drop(db, db.scalars(select(Artifact).where(Artifact.project_id == p.id)))
    base = Path(get_settings().artifact_dir)
    for up in db.scalars(select(Upload).where(Upload.project_id == p.id)):
        safe_join(base, up.object_key).unlink(missing_ok=True)
        stats['bytes'] += up.size_bytes or 0
        stats['files'] += 1
        db.delete(up)
    repo = db.get(Repository, p.repository_id)
    db.delete(p)  # scans, plans, executions, findings, jobs, memberships... (ON DELETE CASCADE)
    db.flush()
    if repo is not None:
        db.delete(repo)
    return stats


def purge_expired(db, days: int | None = None) -> dict:
    days = get_settings().artifact_retention_days if days is None else days
    if not days or days <= 0:
        return {'files': 0, 'bytes': 0, 'days': 0}
    cutoff = now() - timedelta(days=days)
    heavy = list(db.scalars(select(Artifact).where(Artifact.kind.in_(HEAVY_KINDS), Artifact.created_at < cutoff)))
    latest = select(Scan.project_id, func.max(Scan.created_at).label('last')).group_by(Scan.project_id).subquery()
    keep = set(db.scalars(select(Scan.snapshot_artifact_id).join(latest, (Scan.project_id == latest.c.project_id)
                                                                        & (Scan.created_at == latest.c.last))))
    snaps = [a for a in db.scalars(select(Artifact).where(Artifact.kind == 'snapshot', Artifact.created_at < cutoff)) if a.id not in keep]
    return _drop(db, heavy + snaps) | {'days': days}

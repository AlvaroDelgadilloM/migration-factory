"""Odoo Community 12 -> 19 database migrations of an ODOO project (mf.odoo). Everything heavy runs in the worker (job kinds
odoo_analyze / odoo_migrate) and the odoo-hop-<version> runners: restoring the dump, the seven OpenUpgrade hops, reconciliation.

Flow: analyze (modules classified) -> the architect decides on every blocking module (with a reason, audited) -> migrate."""
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, jobs
from ..auth import Principal, authorize, current_principal
from ..db import get_db
from ..errors import ApiError
from ..models import Job, OdooMigration, now
from ..schemas import Dto, JobOut

router = APIRouter(tags=['odoo'])


class OdooMigrationOut(Dto):
    id: uuid.UUID
    project_id: uuid.UUID
    job_id: uuid.UUID | None
    mode: str
    state: str
    analysis_id: uuid.UUID | None = None
    summary: dict
    modules: list
    decisions: dict
    hops: list
    reconciliation: list
    created_by: str
    created_at: datetime
    job: JobOut | None = None


class OdooDecisionIn(Dto):
    module: Annotated[str, Field(pattern=r'^[a-z0-9_]{2,100}$')]
    action: Literal['uninstall', 'pending']   # uninstall: Odoo removes it (and its data) before the first hop; pending: undo
    reason: str = Field(default='', max_length=1000)


def _out(db, om) -> dict:
    d = OdooMigrationOut.model_validate(om).model_dump(mode='json')
    job = db.get(Job, om.job_id) if om.job_id else None
    d['job'] = JobOut.model_validate(job).model_dump(mode='json') if job else None
    return d


def _project(db, me, project_id, perm):
    p = authorize(db, me, project_id, perm)
    if p.migration_type != 'ODOO':
        raise ApiError(409, 'WRONG_MIGRATION_TYPE', 'El proyecto no es Odoo')
    return p


def blocking(om: OdooMigration) -> list[str]:
    """Blocking modules of an analysis still without a decision."""
    return [m['name'] for m in om.modules or [] if m.get('outcome') == 'BLOCK' and (om.decisions or {}).get(m['name'], {}).get('action') != 'uninstall']


def _start(db, me, p, mode, request, analysis=None) -> dict:
    if db.scalar(select(OdooMigration.id).where(OdooMigration.project_id == p.id, OdooMigration.state.in_(('queued', 'running')))):
        raise ApiError(409, 'INVALID_STATE', 'Ya hay un análisis o una migración Odoo en curso para este proyecto')
    om = OdooMigration(project_id=p.id, mode=mode, created_by=me.subject, analysis_id=analysis.id if analysis else None,
                       decisions=dict(analysis.decisions or {}) if analysis else {}, modules=list(analysis.modules or []) if analysis else [])
    db.add(om)
    db.flush()
    job = jobs.create_job(db, kind=f'odoo_{mode}', project_id=p.id, subject_id=om.id, actor=me.subject)
    om.job_id = job.id
    audit.record(db, actor=me.subject, action=f'odoo.{mode}', entity_type='odoo_migration', entity_id=om.id, project_id=p.id,
                 details={'analysis': str(analysis.id) if analysis else None,
                          'uninstall': sorted(k for k, v in (om.decisions or {}).items() if v.get('action') == 'uninstall')}, request=request)
    db.commit()
    jobs.try_publish(db)
    return {'odooMigrationId': str(om.id), 'jobId': str(job.id), 'status': 'queued'}


@router.post('/projects/{project_id}/odoo-analyses', status_code=202)
def analyze(project_id: uuid.UUID, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    """Restore the uploaded dump on the isolated database server and classify every installed module."""
    return _start(db, me, _project(db, me, project_id, 'execute'), 'analyze', request)


@router.post('/projects/{project_id}/odoo-migrations', status_code=202)
def migrate(project_id: uuid.UUID, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    """The seven hops on a copy, from the latest analysis; every blocking module needs the architect's decision first."""
    p = _project(db, me, project_id, 'execute')
    analysis = db.scalar(select(OdooMigration).where(OdooMigration.project_id == p.id, OdooMigration.mode == 'analyze',
                                                     OdooMigration.state == 'succeeded').order_by(OdooMigration.created_at.desc()).limit(1))
    if analysis is None:
        raise ApiError(409, 'ANALYSIS_REQUIRED', 'Primero analice la base (módulos instalados y su estado en 19)')
    pending = blocking(analysis)
    if pending:
        raise ApiError(409, 'MODULES_BLOCKING', f"{len(pending)} módulo(s) sin versión 19 y sin decisión: {', '.join(pending[:10])}",
                       {'modules': pending})
    return _start(db, me, p, 'migrate', request, analysis)


@router.get('/projects/{project_id}/odoo-migrations', response_model=list[OdooMigrationOut])
def list_runs(project_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    _project(db, me, project_id, 'view')
    rows = db.scalars(select(OdooMigration).where(OdooMigration.project_id == project_id).order_by(OdooMigration.created_at.desc()).limit(30))
    return [_out(db, r) for r in rows]


@router.get('/odoo-migrations/{om_id}', response_model=OdooMigrationOut)
def get_run(om_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    om = db.get(OdooMigration, om_id)
    if om is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    _project(db, me, om.project_id, 'view')
    return _out(db, om)


@router.patch('/odoo-migrations/{om_id}/decisions', response_model=OdooMigrationOut)
def decide(om_id: uuid.UUID, body: OdooDecisionIn, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    """The architect's decision on a blocking module (role «plan»): uninstall it before migrating, with the reason."""
    om = db.get(OdooMigration, om_id)
    if om is None or om.mode != 'analyze':
        raise ApiError(404, 'NOT_FOUND', 'Análisis no encontrado')
    authorize(db, me, om.project_id, 'plan')
    if body.module not in {m['name'] for m in om.modules or []}:
        raise ApiError(404, 'NOT_FOUND', 'Ese módulo no está instalado en la base analizada')
    if body.action == 'uninstall' and len(body.reason.strip()) < 10:
        raise ApiError(422, 'VALIDATION_ERROR', 'Indique el motivo (al menos 10 caracteres): sus datos no se migran')
    decisions = dict(om.decisions or {})
    if body.action == 'pending':
        decisions.pop(body.module, None)
    else:
        decisions[body.module] = {'action': 'uninstall', 'reason': body.reason.strip(), 'by': me.subject, 'at': now().isoformat(timespec='seconds')}
    om.decisions = decisions
    audit.record(db, actor=me.subject, action=f'odoo.decision.{body.action}', entity_type='odoo_migration', entity_id=om.id,
                 project_id=om.project_id, details={'module': body.module, 'reason': body.reason}, request=request)
    db.commit()
    return _out(db, om)

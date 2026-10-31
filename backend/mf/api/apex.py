"""Oracle APEX between versions: the applications of an APEX project (mf.apex). The analysis runs in the worker (job kind
apex_analyze); the plan of jumps can be recalculated here for another target without reading the exports again."""
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, jobs
from ..apex import analysis
from ..auth import Principal, authorize, current_principal
from ..db import get_db
from ..errors import ApiError
from ..models import ApexMigration, Job
from ..schemas import Dto, JobOut

router = APIRouter(tags=['apex'])


class ApexAnalysisIn(Dto):
    target: str = Field(default='26.1', pattern=r'^\d{2}\.\d$')
    plan_mode: Literal['yearly', 'all'] = 'yearly'


class ApexMigrationOut(Dto):
    id: uuid.UUID
    project_id: uuid.UUID
    job_id: uuid.UUID | None
    mode: str
    state: str
    target: str
    plan_mode: str
    summary: dict
    apps: list
    plan: dict
    created_by: str
    created_at: datetime
    job: JobOut | None = None


def _out(db, am) -> dict:
    d = ApexMigrationOut.model_validate(am).model_dump(mode='json')
    job = db.get(Job, am.job_id) if am.job_id else None
    d['job'] = JobOut.model_validate(job).model_dump(mode='json') if job else None
    return d


def _project(db, me, project_id, perm):
    p = authorize(db, me, project_id, perm)
    if p.migration_type != 'APEX':
        raise ApiError(409, 'WRONG_MIGRATION_TYPE', 'El proyecto no es Oracle APEX')
    return p


def _check_target(target: str):
    if target not in analysis.catalog()['versions']:
        raise ApiError(422, 'VALIDATION_ERROR', f'Versión de destino desconocida: {target}')


@router.get('/apex/versions')
def versions(me: Principal = Depends(current_principal)):
    """The APEX releases the analysis knows, and the ones a yearly plan goes through."""
    cat = analysis.catalog()
    return {'versions': cat['versions'], 'yearly': cat['yearly'], 'note': cat['note']}


@router.post('/projects/{project_id}/apex-analyses', status_code=202)
def analyze(project_id: uuid.UUID, body: ApexAnalysisIn, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    p = _project(db, me, project_id, 'execute')
    _check_target(body.target)
    if db.scalar(select(ApexMigration.id).where(ApexMigration.project_id == p.id, ApexMigration.state.in_(('queued', 'running')))):
        raise ApiError(409, 'INVALID_STATE', 'Ya hay un análisis en curso para este proyecto')
    am = ApexMigration(project_id=p.id, target=body.target, plan_mode=body.plan_mode, created_by=me.subject)
    db.add(am)
    db.flush()
    job = jobs.create_job(db, kind='apex_analyze', project_id=p.id, subject_id=am.id, actor=me.subject)
    am.job_id = job.id
    audit.record(db, actor=me.subject, action='apex.analyze', entity_type='apex_migration', entity_id=am.id, project_id=p.id,
                 details={'target': body.target, 'planMode': body.plan_mode}, request=request)
    db.commit()
    jobs.try_publish(db)
    return {'apexMigrationId': str(am.id), 'jobId': str(job.id), 'status': 'queued'}


@router.get('/projects/{project_id}/apex-migrations', response_model=list[ApexMigrationOut])
def list_runs(project_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    _project(db, me, project_id, 'view')
    rows = db.scalars(select(ApexMigration).where(ApexMigration.project_id == project_id).order_by(ApexMigration.created_at.desc()).limit(20))
    return [_out(db, r) for r in rows]


@router.get('/apex-migrations/{run_id}/plan')
def plan(run_id: uuid.UUID, target: str = Query(..., pattern=r'^\d{2}\.\d$'), mode: Literal['yearly', 'all'] = 'yearly',
         db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    """The same analysis towards another version, or with every release as a jump."""
    am = db.get(ApexMigration, run_id)
    if am is None:
        raise ApiError(404, 'NOT_FOUND', 'Análisis no encontrado')
    _project(db, me, am.project_id, 'view')
    _check_target(target)
    return analysis.plan(am.apps or [], target, mode)

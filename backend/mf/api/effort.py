"""Effort tracking and estimate. Hours come from rules/effort.json or from the median of recorded time."""
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, effort
from ..auth import Principal, authorize, current_principal
from ..db import get_db
from ..errors import ApiError
from ..models import DiffFile, EffortLog, Endpoint, Execution, Finding, Plan, Route, Scan
from ..schemas import EffortIn, EffortOut
from .common import limit_param, paginate

router = APIRouter(tags=['effort'])


def valid_categories(config):
    return set(config['categories']) | {f'equivalence:{t}' for t in config['equivalence']['hoursByType']} | {'diffReview', 'fixed'}


@router.post('/projects/{project_id}/effort', response_model=EffortOut, status_code=201)
def log_effort(project_id: uuid.UUID, body: EffortIn, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    authorize(db, me, project_id, 'log_effort')
    if body.category not in valid_categories(effort.load_config()):
        raise ApiError(422, 'VALIDATION_ERROR', f'Categoría desconocida: {body.category}')
    e = EffortLog(project_id=project_id, category=body.category, minutes=body.minutes, units=body.units, subject_type=body.subject_type,
                  subject_id=body.subject_id, note=body.note, actor=me.subject)
    db.add(e)
    audit.record(db, actor=me.subject, action='effort.log', entity_type='effort', entity_id=body.subject_id or body.category,
                 project_id=project_id, details={'category': body.category, 'minutes': body.minutes, 'units': body.units}, request=request)
    db.commit()
    return e


@router.get('/projects/{project_id}/effort')
def list_effort(project_id: uuid.UUID, limit: int = limit_param(), cursor: str | None = None,
                db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    authorize(db, me, project_id, 'view')
    stmt = select(EffortLog).where(EffortLog.project_id == project_id).order_by(EffortLog.created_at.desc(), EffortLog.id)
    return paginate(db, stmt, limit, cursor, lambda e: EffortOut.model_validate(e).model_dump(mode='json'))


@router.get('/scans/{scan_id}/effort-estimate')
def scan_estimate(scan_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, scan.project_id, 'view')
    config = effort.load_config()
    findings = [{'ruleId': f.rule_id, 'file': f.file, 'line': f.line} for f in db.scalars(select(Finding).where(Finding.scan_id == scan_id))
                if f.status != 'discarded']
    routes = {r.id: r for r in db.scalars(select(Route).where(Route.scan_id == scan_id))}
    from ..analysis.scanner import INTEGRATION_TYPES
    kind = {c: t for t, cs in INTEGRATION_TYPES.items() for c in cs}
    integrations = [{'type': kind[e.component], 'component': e.component, 'uri': e.redacted_uri, 'file': routes[e.route_id].file}
                    for e in db.scalars(select(Endpoint).where(Endpoint.route_id.in_(list(routes)))) if e.component in kind] if routes else []
    last_apply = db.scalar(select(Execution).join(Plan, Plan.id == Execution.plan_id)
                           .where(Plan.scan_id == scan_id, Execution.mode == 'apply').order_by(Execution.created_at.desc()).limit(1))
    changed = db.scalar(select(func.count()).select_from(DiffFile).where(DiffFile.execution_id == last_apply.id)) if last_apply else None
    units = effort.work_units(findings, integrations, scan.route_builders or [], scan.summary.get('testClasses', 0) if scan.summary else 0,
                              changed, config)
    # calibration uses time recorded across the whole organization (all projects), never invented values
    logs = [{'category': l.category, 'minutes': l.minutes, 'units': l.units} for l in db.scalars(select(EffortLog))]
    est = effort.estimate(units, config, effort.calibrate(logs))
    from ..models import Module
    est['complexity'] = effort.complexity_score({'routes': list(routes.values()), 'integrations': integrations, 'findings': findings,
                                                 'modules': list(db.scalars(select(Module.id).where(Module.scan_id == scan_id)))})
    est['diffBasis'] = 'última ejecución aplicada' if last_apply else 'sin ejecución aplicada: revisión del diff no estimada'
    return est

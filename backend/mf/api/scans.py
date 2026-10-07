import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import PERMISSIONS, Principal, authorize, current_principal, role_in
from ..db import get_db
from ..errors import ApiError
from ..models import Endpoint, Finding, FindingReview, Job, Module, Plan, Route, Scan, TargetProfile
from ..planning import create_plan, evaluate
from ..schemas import (EndpointOut, FindingDetail, Page, Permissions, FindingOut, FindingReviewIn, FindingReviewOut, JobOut, ModuleOut, PlanCreate, PlanOut,
                       RouteOut, ScanOut)
from .common import limit_param, paginate

router = APIRouter(tags=['scans'])

TRANSITIONS = {  # from -> allowed decisions
    'open': {'proposed', 'resolved', 'discarded'},
    'proposed': {'open', 'resolved', 'discarded'},
    'resolved': {'open'},
    'discarded': {'open'},
}


def load_scan(db, me, scan_id, perm='view') -> Scan:
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, scan.project_id, perm)
    return scan


@router.get('/scans/{scan_id}', response_model=ScanOut)
def get_scan(scan_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    scan = load_scan(db, me, scan_id)
    out = ScanOut.model_validate(scan).model_dump(mode='json')
    job = db.get(Job, scan.job_id) if scan.job_id else None
    out['job'] = JobOut.model_validate(job).model_dump(mode='json') if job else None
    return out


@router.get('/scans/{scan_id}/modules', response_model=list[ModuleOut])
def modules(scan_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    load_scan(db, me, scan_id)
    return list(db.scalars(select(Module).where(Module.scan_id == scan_id).order_by(Module.path)))


@router.get('/scans/{scan_id}/routes', response_model=Page[RouteOut])
def routes(scan_id: uuid.UUID, dsl: str | None = Query(None, pattern='^(java|xml)$'), limit: int = limit_param(100),
           cursor: str | None = None, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    load_scan(db, me, scan_id)
    stmt = select(Route).where(Route.scan_id == scan_id).order_by(Route.file, Route.line, Route.id)
    if dsl:
        stmt = stmt.where(Route.dsl == dsl)

    def mapper(r):
        eps = db.scalars(select(Endpoint).where(Endpoint.route_id == r.id).order_by(Endpoint.line))
        d = RouteOut.model_validate(r).model_dump(mode='json')
        d['endpoints'] = [EndpointOut.model_validate(e).model_dump(mode='json') for e in eps]
        return d
    return paginate(db, stmt, limit, cursor, mapper)


@router.get('/scans/{scan_id}/findings', response_model=Page[FindingOut])
def findings(scan_id: uuid.UUID, severity: str | None = Query(None, pattern='^(high|medium|low|info)$'),
             status: str | None = Query(None, pattern='^(open|proposed|resolved|discarded)$'),
             rule: str | None = Query(None, pattern=r'^[A-Z0-9_]{2,60}$'),
             classification: str | None = Query(None, pattern='^(AUTO|AUTO_TEST|REVIEW|MANUAL)$'),
             module: str | None = Query(None, max_length=500), file: str | None = Query(None, max_length=500),
             blocking: bool | None = None,
             limit: int = limit_param(), cursor: str | None = None, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    load_scan(db, me, scan_id)
    stmt = select(Finding).where(Finding.scan_id == scan_id)
    if severity:
        stmt = stmt.where(Finding.severity == severity)
    if status:
        stmt = stmt.where(Finding.status == status)
    if rule:
        stmt = stmt.where(Finding.rule_id == rule)
    if classification:
        stmt = stmt.where(Finding.classification == classification)
    if module:  # module directory prefix, e.g. "orders-routes"
        stmt = stmt.where(Finding.file.startswith(module.rstrip('/') + '/', autoescape=True))
    if file:
        stmt = stmt.where(Finding.file.contains(file, autoescape=True))
    if blocking is not None:
        stmt = stmt.where(Finding.blocking.is_(blocking))
    sev = case({'high': 0, 'medium': 1, 'low': 2, 'info': 3}, value=Finding.severity)
    stmt = stmt.order_by(Finding.blocking.desc(), sev, Finding.file, Finding.line, Finding.id)
    return paginate(db, stmt, limit, cursor, lambda f: FindingOut.model_validate(f).model_dump(mode='json'))


def load_finding(db, me, finding_id, perm='view') -> Finding:
    f = db.get(Finding, finding_id)
    if f is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, f.project_id, perm)
    return f


@router.get('/findings/{finding_id}', response_model=FindingDetail)
def get_finding(finding_id: uuid.UUID, response: Response, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    f = load_finding(db, me, finding_id)
    response.headers['ETag'] = str(f.version)
    out = FindingOut.model_validate(f).model_dump(mode='json')
    out['reviews'] = [FindingReviewOut.model_validate(r).model_dump(mode='json') for r in
                      db.scalars(select(FindingReview).where(FindingReview.finding_id == f.id).order_by(FindingReview.created_at))]
    return out


@router.patch('/findings/{finding_id}/review', response_model=FindingOut)
def review_finding(finding_id: uuid.UUID, body: FindingReviewIn, request: Request, response: Response, if_match: str = Header(...),
                   db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    f = load_finding(db, me, finding_id, 'view')
    perm = 'propose' if body.decision == 'proposed' else 'review'
    authorize(db, me, f.project_id, perm)
    if if_match.strip('"') != str(f.version):
        raise ApiError(409, 'VERSION_CONFLICT', 'El hallazgo cambió; recargue', {'currentVersion': f.version})
    if body.decision not in TRANSITIONS[f.status]:
        raise ApiError(409, 'INVALID_TRANSITION', f'No se puede pasar de {f.status} a {body.decision}')
    before = {'status': f.status, 'version': f.version}
    db.add(FindingReview(finding_id=f.id, reviewer=me.subject, from_status=f.status, decision=body.decision, reason=body.reason))
    if body.minutes:
        from .. import effort
        from ..models import EffortLog
        cat = effort.category_of(f.rule_id, effort.load_config())
        if cat:
            db.add(EffortLog(project_id=f.project_id, category=cat, minutes=body.minutes, units=1, subject_type='finding',
                             subject_id=str(f.id), note=f'{f.rule_id} {f.file}', actor=me.subject))
    f.status, f.version = body.decision, f.version + 1
    audit.record(db, actor=me.subject, action='finding.review', entity_type='finding', entity_id=f.id, project_id=f.project_id,
                 before=before, after={'status': f.status, 'version': f.version},
                 details={'rule': f.rule_id, 'from': before['status'], 'to': f.status, 'reason': body.reason[:500]}, request=request)
    db.commit()
    response.headers['ETag'] = str(f.version)
    return FindingOut.model_validate(f).model_dump(mode='json')


def plan_out(db, plan: Plan) -> dict:
    d = PlanOut.model_validate(plan).model_dump(mode='json')
    d['steps'] = evaluate(db, plan)
    prof = db.get(TargetProfile, plan.profile_id)
    d['profile'] = {'id': str(prof.id), 'key': prof.key, 'version': prof.version, 'name': prof.name, 'status': prof.status,
                    'runtime': prof.runtime, 'digest': prof.digest}
    return d


@router.post('/scans/{scan_id}/plans', status_code=201, response_model=PlanOut)
def new_plan(scan_id: uuid.UUID, body: PlanCreate, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    scan = load_scan(db, me, scan_id, 'plan')
    if scan.status != 'succeeded':
        raise ApiError(409, 'INVALID_STATE', 'El análisis no ha terminado correctamente')
    ws = (scan.summary or {}).get('workspace') or {}
    if ws.get('repositoryType') == 'MULTI_PROJECT_REPOSITORY' and not ws.get('selectedProject'):  # doc 17: plans need one project
        raise ApiError(409, 'ROOT_SELECTION_REQUIRED', 'El origen es un workspace con varios proyectos: analice un proyecto concreto para planificar',
                       {'projects': [p['dir'] for p in ws.get('projects', [])], 'order': (ws.get('order') or {}).get('order', [])})
    profile = db.get(TargetProfile, body.profile_id)
    if profile is None:
        raise ApiError(404, 'NOT_FOUND', 'Perfil inexistente')
    if profile.status != 'validated':
        raise ApiError(409, 'PROFILE_NOT_VALIDATED', 'Solo se planifica con perfiles validados')
    plan = create_plan(db, scan, profile, me.subject)
    audit.record(db, actor=me.subject, action='plan.create', entity_type='plan', entity_id=plan.id, project_id=scan.project_id,
                 after={'digest': plan.digest}, details={'profile': f'{profile.key}@{profile.version}', 'version': plan.version}, request=request)
    db.commit()
    return plan_out(db, plan)


@router.get('/scans/{scan_id}/plans', response_model=list[PlanOut])
def list_plans(scan_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    load_scan(db, me, scan_id)
    return [PlanOut.model_validate(p).model_dump(mode='json') for p in
            db.scalars(select(Plan).where(Plan.scan_id == scan_id).order_by(Plan.version.desc()))]


@router.get('/projects/{project_id}/permissions', response_model=Permissions)
def my_permissions(project_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    authorize(db, me, project_id, 'view')
    role = role_in(db, me, project_id)
    return {'role': role, 'isAdmin': me.is_admin,
            'permissions': sorted(p for p, roles in PERMISSIONS.items() if me.is_admin or role in roles)}

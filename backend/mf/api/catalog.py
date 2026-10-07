import csv
import hashlib
import io
import json
import re
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import DEV_USERS, Principal, authorize, current_principal, issue_dev_token, require_admin, role_in, visible_project_ids
from ..config import get_settings
from ..db import get_db
from ..errors import ApiError
from ..models import AuditEvent, RuleCatalog, TargetProfile
from ..profiles import profile_digest
from ..schemas import AuditOut, AuthConfig, Page, CatalogCreate, CatalogOut, Me, ProfileCreate, ProfileOut
from .common import limit_param, paginate

router = APIRouter()


@router.get('/plugins')
def list_plugins(me: Principal = Depends(current_principal)):
    """improvements/10: plugin contract of this engine (id, version, capabilities, sources, targets, dependencies)."""
    from ..plugins import PLUGINS
    return PLUGINS


# --- auth ---------------------------------------------------------------------------------------
class DevLogin(BaseModel):
    username: str


@router.get('/auth/config', tags=['auth'], response_model=AuthConfig)
def auth_config():
    """Public: tells the UI which login flow to use. Contains no secrets."""
    s = get_settings()
    return {'mode': s.auth_mode, 'issuer': s.oidc_issuer or None, 'clientId': s.oidc_client_id if s.auth_mode == 'oidc' else None,
            'devUsers': [{'username': k, 'name': v['name']} for k, v in DEV_USERS.items()] if s.auth_mode == 'dev' else []}


@router.post('/auth/dev-login', tags=['auth'])
def dev_login(body: DevLogin):
    if get_settings().auth_mode != 'dev':
        raise ApiError(404, 'NOT_FOUND', 'No disponible')
    return {'accessToken': issue_dev_token(body.username), 'tokenType': 'Bearer'}


@router.get('/me', response_model=Me, tags=['auth'])
def me(principal: Principal = Depends(current_principal)):
    return Me(subject=principal.subject, name=principal.name, is_admin=principal.is_admin, auth_mode=get_settings().auth_mode)


# --- profiles -----------------------------------------------------------------------------------
@router.get('/profiles', response_model=list[ProfileOut], tags=['profiles'])
def list_profiles(status: str | None = Query(None, pattern='^(draft|validated|retired)$'), runtime: str | None = Query(None, pattern='^(spring|quarkus)$'),
                  db: Session = Depends(get_db), _: Principal = Depends(current_principal)):
    stmt = select(TargetProfile).order_by(TargetProfile.key, TargetProfile.version.desc())
    if status:
        stmt = stmt.where(TargetProfile.status == status)
    if runtime:
        stmt = stmt.where(TargetProfile.runtime == runtime)
    return list(db.scalars(stmt))


@router.post('/profiles', response_model=ProfileOut, status_code=201, tags=['profiles'])
def create_profile(body: ProfileCreate, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    """New immutable profile version (draft). Existing versions are never edited."""
    require_admin(me)
    tooling = body.tooling
    if not re.fullmatch(r'\d+\.\d+\.\d+', str(tooling.get('pluginVersion', ''))):
        raise ApiError(422, 'VALIDATION_ERROR', 'tooling.pluginVersion debe ser una versión fija')
    for a in tooling.get('artifacts', []):
        if not re.fullmatch(r'[\w.\-]+:[\w.\-]+:\d[\w.\-]*', a):
            raise ApiError(422, 'VALIDATION_ERROR', f'Artefacto de recetas no fijado: {a}')
    last = db.scalar(select(func.max(TargetProfile.version)).where(TargetProfile.key == body.key)) or 0
    data = body.model_dump(by_alias=False) | {'version': last + 1, 'status': 'draft'}
    p = TargetProfile(**data, digest=profile_digest(data))
    db.add(p)
    audit.record(db, actor=me.subject, action='profile.create', entity_type='profile', entity_id=f'{p.key}@{p.version}', after={'digest': p.digest}, request=request)
    db.commit()
    return p


@router.post('/profiles/{profile_id}/status', response_model=ProfileOut, tags=['profiles'])
def set_profile_status(profile_id: uuid.UUID, status: str = Query(pattern='^(validated|retired)$'), request: Request = None,
                       db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    require_admin(me)
    p = db.get(TargetProfile, profile_id)
    if p is None:
        raise ApiError(404, 'NOT_FOUND', 'Perfil inexistente')
    allowed = {'draft': {'validated', 'retired'}, 'validated': {'retired'}, 'retired': set()}
    if status not in allowed[p.status]:
        raise ApiError(409, 'INVALID_TRANSITION', f'No se puede pasar de {p.status} a {status}')
    before, p.status = p.status, status
    audit.record(db, actor=me.subject, action='profile.status', entity_type='profile', entity_id=f'{p.key}@{p.version}',
                 before={'status': before}, after={'status': status}, request=request)
    db.commit()
    return p


# --- rule catalogs ------------------------------------------------------------------------------
def check_rules(rules):
    """Each text rule must compile and pass its own positive/negative examples."""
    errors = []
    for r in rules:
        missing = {'id', 'version', 'detector', 'severity', 'classification', 'recommendation'} - set(r)
        if missing:
            errors.append(f"{r.get('id', '?')}: faltan {sorted(missing)}")
            continue
        if r['severity'] not in ('high', 'medium', 'low', 'info') or r['classification'] not in ('AUTO', 'AUTO_TEST', 'REVIEW', 'MANUAL'):
            errors.append(f"{r['id']}: severidad/clasificación inválida")
        if r['detector'] == 'text':
            try:
                rx = re.compile(r.get('pattern', ''))
            except re.error as e:
                errors.append(f"{r['id']}: regex inválida ({e})")
                continue
            ex = r.get('examples') or {}
            if not ex.get('positive'):
                errors.append(f"{r['id']}: requiere ejemplos positivos")
            errors += [f"{r['id']}: no detecta {x!r}" for x in ex.get('positive', []) if not rx.search(x)]
            try:
                excl = re.compile(r['excludeLine']) if r.get('excludeLine') else None
            except re.error as e:
                errors.append(f"{r['id']}: excludeLine inválida ({e})")
                continue
            errors += [f"{r['id']}: detecta negativo {x!r}" for x in ex.get('negative', []) if rx.search(x) and not (excl and excl.search(x))]
    return errors


@router.get('/rules', response_model=list[CatalogOut], tags=['rules'])
def list_catalogs(db: Session = Depends(get_db), _: Principal = Depends(current_principal)):
    return list(db.scalars(select(RuleCatalog).order_by(RuleCatalog.created_at.desc())))


@router.post('/rules', response_model=CatalogOut, status_code=201, tags=['rules'])
def create_catalog(body: CatalogCreate, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    require_admin(me)
    if db.scalar(select(RuleCatalog).where(RuleCatalog.version == body.catalog_version)):
        raise ApiError(409, 'CONFLICT', 'La versión ya existe; el histórico no se edita')
    errors = check_rules(body.rules)
    if errors:
        raise ApiError(422, 'RULES_INVALID', 'El catálogo no pasa sus pruebas', {'errors': errors[:50]})
    digest = hashlib.sha256(json.dumps(body.rules, sort_keys=True, default=str).encode()).hexdigest()
    c = RuleCatalog(version=body.catalog_version, digest=digest, source_uri='api', status='draft', rules=body.rules)
    db.add(c)
    audit.record(db, actor=me.subject, action='catalog.create', entity_type='rule_catalog', entity_id=body.catalog_version, after={'digest': digest}, request=request)
    db.commit()
    return c


@router.post('/rules/{catalog_id}/activate', response_model=CatalogOut, tags=['rules'])
def activate_catalog(catalog_id: uuid.UUID, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    require_admin(me)
    c = db.get(RuleCatalog, catalog_id)
    if c is None:
        raise ApiError(404, 'NOT_FOUND', 'Catálogo inexistente')
    if c.status != 'draft':
        raise ApiError(409, 'INVALID_TRANSITION', f'Solo se activa un borrador (estado {c.status})')
    for other in db.scalars(select(RuleCatalog).where(RuleCatalog.status == 'active')):
        other.status = 'retired'
    c.status = 'active'
    audit.record(db, actor=me.subject, action='catalog.activate', entity_type='rule_catalog', entity_id=c.version, request=request)
    db.commit()
    return c


# --- audit --------------------------------------------------------------------------------------
def _audit_stmt(db, me, project_id, action, entity_type):
    stmt = select(AuditEvent)
    if project_id:
        authorize(db, me, project_id, 'audit')
        stmt = stmt.where(AuditEvent.project_id == project_id)
    elif not me.is_admin:
        ids = [pid for pid in visible_project_ids(db, me) if role_in(db, me, pid) in ('owner', 'architect', 'auditor')]
        stmt = stmt.where(AuditEvent.project_id.in_(ids))
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action, autoescape=True))
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    return stmt.order_by(AuditEvent.id.desc())


@router.get('/audit', tags=['audit'], response_model=Page[AuditOut])
def list_audit(project_id: uuid.UUID | None = Query(None, alias='projectId'), action: str | None = Query(None, max_length=80),
               entity_type: str | None = Query(None, max_length=40, alias='entityType'), limit: int = limit_param(), cursor: str | None = None,
               db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    stmt = _audit_stmt(db, me, project_id, action, entity_type)
    return paginate(db, stmt, limit, cursor, lambda a: AuditOut.model_validate(a).model_dump(mode='json'))


@router.get('/audit/export', tags=['audit'])
def export_audit(request: Request, project_id: uuid.UUID | None = Query(None, alias='projectId'), db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    stmt = _audit_stmt(db, me, project_id, None, None).limit(50_000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['id', 'at', 'project_id', 'actor', 'action', 'entity_type', 'entity_id', 'before_hash', 'after_hash', 'request_id'])
    for a in db.scalars(stmt):
        w.writerow([a.id, a.at.isoformat(), a.project_id or '', a.actor, a.action, a.entity_type, a.entity_id,
                    a.before_hash or '', a.after_hash or '', a.request_id or ''])
    audit.record(db, actor=me.subject, action='audit.export', entity_type='audit', entity_id=str(project_id or 'all'),
                 project_id=project_id, request=request)
    db.commit()
    return Response(buf.getvalue(), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="audit.csv"'})


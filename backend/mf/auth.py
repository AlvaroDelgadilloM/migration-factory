"""Authentication (OIDC bearer or dev tokens) and per-project authorization."""
import time
import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .errors import ApiError
from .models import AppUser, Membership, Project, now

# Fixture identities for AUTH_MODE=dev only (demo / tests). Never used in oidc mode.
DEV_USERS = {
    'admin': {'name': 'Admin Plataforma', 'admin': True},
    'ana.arquitecta': {'name': 'Ana (arquitecta)', 'admin': False},
    'diego.dev': {'name': 'Diego (desarrollador)', 'admin': False},
    'rosa.revisora': {'name': 'Rosa (revisora)', 'admin': False},
    'aldo.auditor': {'name': 'Aldo (auditor)', 'admin': False},
    'mallory.externa': {'name': 'Mallory (sin membresía)', 'admin': False},
}

ROLES = ('owner', 'architect', 'developer', 'reviewer', 'auditor')
PERMISSIONS = {
    'view': set(ROLES),
    'audit': {'owner', 'architect', 'auditor'},
    'scan': {'owner', 'architect', 'developer'},
    'propose': {'owner', 'architect', 'developer'},   # finding -> proposed
    'review': {'owner', 'architect', 'reviewer'},     # finding -> resolved / discarded / reopen
    'plan': {'owner', 'architect'},
    'execute': {'owner', 'architect', 'developer'},
    'approve_diff': {'owner', 'architect', 'reviewer'},
    'evidence': {'owner', 'architect', 'reviewer'},
    'pull_request': {'owner', 'architect', 'reviewer'},
    'manage': {'owner'},
    'log_effort': {'owner', 'architect', 'developer', 'reviewer'},
}


@dataclass
class Principal:
    subject: str
    name: str
    email: str | None
    is_admin: bool


_jwks_clients = {}


def _decode_oidc(token: str) -> dict:
    s = get_settings()
    jwks_url = s.oidc_jwks_url
    if not jwks_url:  # OIDC discovery, cached with the client below
        jwks_url = _jwks_clients.get('discovered')
        if not jwks_url:
            import httpx
            meta = httpx.get(s.oidc_issuer.rstrip('/') + '/.well-known/openid-configuration', timeout=10).json()
            jwks_url = _jwks_clients['discovered'] = meta['jwks_uri']
    client = _jwks_clients.get(jwks_url)
    if client is None:
        client = _jwks_clients[jwks_url] = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=300)
    key = client.get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=['RS256', 'ES256', 'PS256'], audience=s.oidc_audience,
                      issuer=s.oidc_issuer, options={'require': ['exp', 'iat', 'sub']}, leeway=30)


def _roles(claims) -> set:
    roles = set(claims.get('roles') or []) | set(claims.get('groups') or [])
    roles |= set((claims.get('realm_access') or {}).get('roles') or [])
    return roles


def issue_dev_token(username: str) -> str:
    s = get_settings()
    if s.auth_mode != 'dev' or username not in DEV_USERS:
        raise ApiError(404, 'NOT_FOUND', 'Usuario de demostración inexistente')
    t = int(time.time())
    u = DEV_USERS[username]
    claims = {'sub': username, 'name': u['name'], 'iat': t, 'exp': t + 8 * 3600, 'iss': 'mf-dev', 'aud': 'mf-dev',
              'roles': ['mf-admin'] if u['admin'] else [], 'jti': str(uuid.uuid4())}
    return jwt.encode(claims, s.dev_jwt_secret, algorithm='HS256')


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    s = get_settings()
    header = request.headers.get('authorization', '')
    if not header.lower().startswith('bearer '):
        raise ApiError(401, 'UNAUTHENTICATED', 'Falta token bearer')
    token = header[7:].strip()
    try:
        if s.auth_mode == 'dev':
            claims = jwt.decode(token, s.dev_jwt_secret, algorithms=['HS256'], audience='mf-dev', issuer='mf-dev',
                                options={'require': ['exp', 'sub']})
            is_admin = 'mf-admin' in claims.get('roles', [])
        else:
            claims = _decode_oidc(token)
            is_admin = s.oidc_admin_role in _roles(claims)
    except jwt.PyJWTError:
        raise ApiError(401, 'UNAUTHENTICATED', 'Token inválido o expirado') from None
    p = Principal(claims['sub'], claims.get('name') or claims.get('preferred_username') or claims['sub'],
                  claims.get('email'), is_admin)
    user = db.get(AppUser, p.subject)
    if user is None:
        db.add(AppUser(id=p.subject, display_name=p.name, email=p.email, is_admin=p.is_admin))
    else:
        user.display_name, user.email, user.is_admin, user.last_seen = p.name, p.email, p.is_admin, now()
    db.commit()
    request.state.subject = p.subject
    return p


def role_in(db: Session, principal: Principal, project_id) -> str | None:
    m = db.get(Membership, (project_id, principal.subject))
    return m.role if m else None


def authorize(db: Session, principal: Principal, project_id, permission: str) -> Project:
    """404 when the project is not visible (no membership), 403 when visible but not permitted."""
    project = db.get(Project, project_id)
    if project is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    if principal.is_admin:
        return project
    role = role_in(db, principal, project_id)
    if role is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    if role not in PERMISSIONS[permission]:
        raise ApiError(403, 'FORBIDDEN', f'El rol {role} no permite {permission}', {'required': sorted(PERMISSIONS[permission])})
    return project


def visible_project_ids(db: Session, principal: Principal):
    if principal.is_admin:
        return None  # all
    return list(db.scalars(select(Membership.project_id).where(Membership.subject_id == principal.subject)))


def require_admin(principal: Principal):
    if not principal.is_admin:
        raise ApiError(403, 'FORBIDDEN', 'Requiere administrador de plataforma')

import uuid

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit, jobs
from ..auth import Principal, authorize, current_principal, role_in, visible_project_ids
from ..db import get_db
from ..errors import ApiError
from ..models import AppUser, Membership, Organization, Project, Repository, RuleCatalog, Scan, Upload
from ..schemas import MemberIn, MemberOut, Page, ProjectCreate, ProjectOut, ProjectUpdate, ScanAccepted, ScanCreate, ScanOut
from ..security import UnsafeInput, classify_source, validate_credential_ref, validate_git_url, validate_local_path
from .common import limit_param, paginate

router = APIRouter(tags=['projects'])


def project_out(db, p: Project, principal: Principal) -> dict:
    repo = db.get(Repository, p.repository_id)
    last = db.scalar(select(Scan).where(Scan.project_id == p.id).order_by(Scan.created_at.desc()).limit(1))
    last_ok = db.scalar(select(Scan).where(Scan.project_id == p.id, Scan.status == 'succeeded').order_by(Scan.created_at.desc()).limit(1))
    up = db.get(Upload, repo.upload_id) if repo.upload_id else None
    d = ProjectOut(id=p.id, name=p.name, description=p.description, source_type=repo.source_type, repository_url=repo.url,
                   repository_provider=repo.provider, credential_ref=repo.credential_ref, local_path=repo.local_path,
                   upload={'id': str(up.id), 'filename': up.filename, 'sha256': up.sha256, 'sizeBytes': up.size_bytes,
                           'structure': up.structure} if up else None,
                   default_branch=p.default_branch, target_runtime=p.target_runtime,
                   required_gates=p.required_gates, independent_review=p.independent_review, pr_config=p.pr_config,
                   my_role='admin' if principal.is_admin and not role_in(db, principal, p.id) else role_in(db, principal, p.id),
                   version=p.version, created_at=p.created_at).model_dump()
    if last:
        d['lastScan'] = {'id': str(last.id), 'status': last.status, 'commitSha': last.commit_sha, 'snapshotHash': last.snapshot_hash,
                         'createdAt': last.created_at.isoformat(),
                         'findings': (last.summary or {}).get('findings'),
                         'lastSucceededId': str(last_ok.id) if last_ok else None}
    return d


def default_org(db) -> Organization:
    org = db.scalar(select(Organization).where(Organization.name == 'default'))
    if org is None:
        org = Organization(name='default')
        db.add(org)
        db.flush()
    return org


@router.post('/projects', status_code=201, response_model=ProjectOut)
def create_project(body: ProjectCreate, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    upload = None
    try:
        if body.source_type == 'git':
            info = validate_git_url(body.repository_url)
            validate_credential_ref(body.credential_ref)
            repo = Repository(source_type='git', provider=info['provider'], url=info['url'], credential_ref=body.credential_ref)
        elif body.source_type == 'local':
            path = validate_local_path(body.local_path)
            files = [str(f.relative_to(path)) for f in path.rglob('pom.xml') if 'target' not in f.relative_to(path).parts][:5000]
            classify_source(files)  # several projects without aggregator = workspace (doc 17), not an error
            repo = Repository(source_type='local', provider='local', local_path=str(path))
        else:
            upload = db.get(Upload, body.upload_id)
            if upload is None or upload.owner != me.subject:
                raise ApiError(404, 'NOT_FOUND', 'Archivo subido no encontrado')
            if upload.project_id:
                raise ApiError(409, 'UPLOAD_IN_USE', 'Este archivo ya está asociado a otro proyecto')
            repo = Repository(source_type='zip', provider='zip', upload_id=upload.id)
    except UnsafeInput as e:
        raise ApiError(422, e.code, str(e)) from None
    db.add(repo)
    db.flush()
    p = Project(organization_id=default_org(db).id, name=body.name, description=body.description, repository_id=repo.id,
                default_branch=body.default_branch or '-', target_runtime=body.target_runtime, created_by=me.subject)
    db.add(p)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, 'CONFLICT', 'Ya existe un proyecto con ese nombre') from None
    if upload:
        upload.project_id = p.id
    db.add(Membership(project_id=p.id, subject_id=me.subject, role='owner'))
    audit.record(db, actor=me.subject, action='project.create', entity_type='project', entity_id=p.id, project_id=p.id,
                 after={'name': p.name, 'source': repo.source_type, 'url': repo.url, 'path': repo.local_path,
                        'upload': upload and upload.sha256}, request=request)
    db.commit()
    return project_out(db, p, me)


@router.get('/projects', response_model=Page[ProjectOut])
def list_projects(q: str | None = None, limit: int = limit_param(), cursor: str | None = None,
                  db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    stmt = select(Project).order_by(Project.name, Project.id)
    ids = visible_project_ids(db, me)
    if ids is not None:
        stmt = stmt.where(Project.id.in_(ids))
    if q:
        stmt = stmt.where(Project.name.ilike(f'%{q[:100]}%'))
    return paginate(db, stmt, limit, cursor, lambda p: project_out(db, p, me))


@router.get('/projects/{project_id}', response_model=ProjectOut)
def get_project(project_id: uuid.UUID, response: Response, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    p = authorize(db, me, project_id, 'view')
    response.headers['ETag'] = str(p.version)
    return project_out(db, p, me)


@router.patch('/projects/{project_id}', response_model=ProjectOut)
def update_project(project_id: uuid.UUID, body: ProjectUpdate, request: Request, if_match: str = Header(...),
                   db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    p = authorize(db, me, project_id, 'manage')
    if if_match.strip('"') != str(p.version):
        raise ApiError(409, 'VERSION_CONFLICT', 'El proyecto cambió; recargue', {'currentVersion': p.version})
    before = {'gates': p.required_gates, 'review': p.independent_review, 'pr': p.pr_config, 'runtime': p.target_runtime}
    data = body.model_dump(exclude_unset=True, by_alias=False)
    for k, v in data.items():
        setattr(p, k, v)
    p.version += 1
    audit.record(db, actor=me.subject, action='project.update', entity_type='project', entity_id=p.id, project_id=p.id,
                 before=before, after=data, details={'fields': sorted(data)}, request=request)
    db.commit()
    return project_out(db, p, me)


@router.get('/projects/{project_id}/members', response_model=list[MemberOut])
def list_members(project_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    authorize(db, me, project_id, 'view')
    rows = db.execute(select(Membership, AppUser.display_name).outerjoin(AppUser, AppUser.id == Membership.subject_id)
                      .where(Membership.project_id == project_id).order_by(Membership.subject_id))
    return [MemberOut(subject_id=m.subject_id, role=m.role, display_name=n) for m, n in rows]


@router.put('/projects/{project_id}/members', response_model=MemberOut)
def put_member(project_id: uuid.UUID, body: MemberIn, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    authorize(db, me, project_id, 'manage')
    m = db.get(Membership, (project_id, body.subject_id))
    before = m and m.role
    if m is None:
        m = Membership(project_id=project_id, subject_id=body.subject_id, role=body.role)
        db.add(m)
    else:
        m.role = body.role
    audit.record(db, actor=me.subject, action='membership.set', entity_type='membership', entity_id=body.subject_id,
                 project_id=project_id, before=before, after=body.role, details={'role': body.role, 'previous': before}, request=request)
    db.commit()
    return MemberOut(subject_id=m.subject_id, role=m.role)


@router.delete('/projects/{project_id}/members/{subject_id}', status_code=204)
def delete_member(project_id: uuid.UUID, subject_id: str, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    authorize(db, me, project_id, 'manage')
    m = db.get(Membership, (project_id, subject_id))
    if m is None:
        raise ApiError(404, 'NOT_FOUND', 'Miembro no encontrado')
    owners = db.scalars(select(Membership).where(Membership.project_id == project_id, Membership.role == 'owner')).all()
    if m.role == 'owner' and len(owners) == 1:
        raise ApiError(409, 'INVALID_STATE', 'No se puede retirar al único owner')
    db.delete(m)
    audit.record(db, actor=me.subject, action='membership.remove', entity_type='membership', entity_id=subject_id,
                 project_id=project_id, before=m.role, request=request)
    db.commit()


@router.post('/projects/{project_id}/scans', status_code=202, response_model=ScanAccepted)
def create_scan(project_id: uuid.UUID, body: ScanCreate, request: Request, idempotency_key: str | None = Header(None),
                db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    p = authorize(db, me, project_id, 'scan')
    endpoint = f'POST /projects/{project_id}/scans'
    payload = body.model_dump(mode='json')
    replay = jobs.idempotent_replay(db, me.subject, idempotency_key, endpoint, payload)
    if replay:
        return replay
    catalog = db.get(RuleCatalog, body.catalog_id) if body.catalog_id else \
        db.scalar(select(RuleCatalog).where(RuleCatalog.status == 'active').order_by(RuleCatalog.created_at.desc()))
    if catalog is None or catalog.status == 'draft':
        raise ApiError(422, 'CATALOG_INVALID', 'Catálogo de reglas inexistente o en borrador')
    running = db.scalar(select(Scan).where(Scan.project_id == p.id, Scan.status.in_(['queued', 'running'])).limit(1))
    if running:
        raise ApiError(409, 'SCAN_IN_PROGRESS', 'Ya hay un análisis en curso para este proyecto', {'scanId': str(running.id)})
    repo = db.get(Repository, p.repository_id)
    if repo.source_type != 'git' and body.ref:
        raise ApiError(422, 'VALIDATION_ERROR', 'La referencia (rama/commit) solo aplica a orígenes Git')
    scan = Scan(project_id=p.id, ref=(body.ref or p.default_branch) if repo.source_type == 'git' else repo.source_type, catalog_id=catalog.id, target=body.target,
                created_by=me.subject, project_root=body.project_root)  # requested project; the worker validates it
    db.add(scan)
    db.flush()
    job = jobs.create_job(db, kind='scan', project_id=p.id, subject_id=scan.id, actor=me.subject)
    scan.job_id = job.id
    result = {'scanId': str(scan.id), 'jobId': str(job.id), 'status': 'queued'}
    jobs.remember(db, me.subject, idempotency_key, endpoint, payload, 202, result)
    audit.record(db, actor=me.subject, action='scan.request', entity_type='scan', entity_id=scan.id, project_id=p.id,
                 details={'ref': scan.ref, 'catalog': catalog.version, 'projectRoot': body.project_root}, request=request)
    db.commit()
    jobs.try_publish(db)
    return result


@router.get('/projects/{project_id}/scans', response_model=Page[ScanOut])
def list_scans(project_id: uuid.UUID, limit: int = limit_param(20), cursor: str | None = None,
               db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    authorize(db, me, project_id, 'view')
    stmt = select(Scan).where(Scan.project_id == project_id).order_by(Scan.created_at.desc(), Scan.id)
    return paginate(db, stmt, limit, cursor, lambda s: ScanOut.model_validate(s).model_dump(mode='json'))

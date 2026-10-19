"""ZIP uploads (raw body, no multipart dependency) and source capabilities."""
import hashlib
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Principal, current_principal
from ..config import get_settings
from ..db import get_db
from ..errors import ApiError
from ..models import Upload
from ..schemas import BrowseOut, Capabilities, UploadOut
from ..security import UnsafeInput, inspect_zip, local_roots, safe_join, validate_local_path

router = APIRouter(tags=['sources'])


@router.get('/capabilities', response_model=Capabilities)
def capabilities(_: Principal = Depends(current_principal)):
    s = get_settings()
    return Capabilities(local_source_enabled=bool(local_roots()), local_source_roots=[str(r) for r in local_roots()],
                        max_upload_mb=s.max_upload_mb, git_allowed_hosts=s.list_of(s.git_allowed_hosts))


BROWSE_SKIP = {'target', 'node_modules', '__MACOSX'}
BROWSE_MAX = 500


@router.get('/sources/browse', response_model=BrowseOut)
def browse(path: str | None = Query(None, max_length=1000), _: Principal = Depends(current_principal)):
    """Folders as the worker sees them, only inside MF_LOCAL_SOURCE_ROOTS. Directory names only (no files, no
    contents); symlinked and hidden folders are not listed; '..' or links leaving a root are rejected."""
    roots = local_roots()
    if not roots:
        raise ApiError(422, 'LOCAL_SOURCE_DISABLED', 'Carpetas locales deshabilitadas en esta instalación')
    if not path:
        return BrowseOut(path=None, parent=None, has_pom=False,
                         entries=[{'name': str(r), 'path': str(r), 'has_pom': (r / 'pom.xml').is_file()} for r in roots if r.is_dir()])
    try:
        p = validate_local_path(path)
    except UnsafeInput as e:
        raise ApiError(422, e.code, str(e)) from None
    root = next(r for r in roots if p == r or p.is_relative_to(r))
    entries = []
    try:
        children = sorted(p.iterdir(), key=lambda c: c.name.lower())
    except PermissionError:
        raise ApiError(403, 'FORBIDDEN', 'Sin permiso de lectura en esa carpeta') from None
    for c in children:
        if c.name.startswith('.') or c.name in BROWSE_SKIP or c.is_symlink() or not c.is_dir():
            continue
        entries.append({'name': c.name, 'path': str(c), 'has_pom': (c / 'pom.xml').is_file()})
        if len(entries) >= BROWSE_MAX:
            break
    return BrowseOut(path=str(p), parent=None if p == root else str(p.parent), has_pom=(p / 'pom.xml').is_file(),
                     entries=entries, truncated=len(entries) >= BROWSE_MAX)


def _kinds(files) -> str:
    """What a ZIP contains, by extension (for error messages that say what was found, not only what is missing)."""
    from collections import Counter
    kinds = Counter((Path(f).suffix.lower() or 'sin extensión') for f in files if not f.endswith('/'))
    return ', '.join(f'{n} {ext}' for ext, n in kinds.most_common(5)) or 'nada'


@router.post('/uploads', response_model=UploadOut, status_code=201)
async def upload_zip(request: Request, x_filename: str = Header('project.zip', max_length=255),
                     x_migration_type: str = Header('JAVA_CAMEL', pattern=r'^(AUTO|JAVA_CAMEL|DATASTAGE_AIRFLOW)$'),
                     content_length: int | None = Header(None), db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    """Body = the ZIP bytes (Content-Type: application/zip). Size enforced while streaming, structure validated before storing."""
    s = get_settings()
    limit = s.max_upload_mb * 1024 * 1024
    if content_length and content_length > limit:
        raise ApiError(413, 'UPLOAD_TOO_LARGE', f'Máximo {s.max_upload_mb} MB')
    up_id = uuid.uuid4()
    key = f'uploads/{up_id.hex}.zip'
    target = safe_join(Path(s.artifact_dir), key)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix='.part')
    try:
        with os.fdopen(fd, 'wb') as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise ApiError(413, 'UPLOAD_TOO_LARGE', f'Máximo {s.max_upload_mb} MB')
                digest.update(chunk)
                f.write(chunk)
        if size == 0:
            raise ApiError(422, 'ZIP_INVALID', 'Archivo vacío')
        try:
            mode = x_migration_type
            probe = None
            if mode == 'AUTO':  # the platform decides from the content: .dsx = DataStage export, else a Maven project
                probe = inspect_zip(Path(tmp), require_maven=False)
                mode = 'DATASTAGE_AIRFLOW' if any(f.lower().endswith('.dsx') for f in probe['files']) else 'JAVA_CAMEL'
            datastage = mode == 'DATASTAGE_AIRFLOW'
            try:  # metadata only: nothing is extracted here
                structure = probe if (datastage and probe) else inspect_zip(Path(tmp), require_maven=not datastage)
            except UnsafeInput as e:
                if x_migration_type != 'AUTO':
                    raise
                raise UnsafeInput(e.code, f'{e} El ZIP no trae un proyecto Maven (pom.xml) ni exportaciones DataStage (.dsx); '
                                          f'trae: {_kinds(probe["files"])}.') from None
            if datastage:
                dsx = [f for f in structure['files'] if f.lower().endswith('.dsx')]
                if not dsx:
                    raise UnsafeInput('NO_DSX', f'El ZIP no contiene exportaciones DataStage (.dsx); trae: {_kinds(structure.get("files", []))}. '
                                                'Suba el ZIP con los .dsx exportados de DataStage.')
                structure.pop('files')
                structure['dsxFiles'] = dsx[:500]
            structure['migrationType'] = mode
        except UnsafeInput as e:
            raise ApiError(422, e.code, str(e)) from None
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    from urllib.parse import unquote
    name = Path(unquote(x_filename)).name[:200] or 'project.zip'
    up = Upload(id=up_id, owner=me.subject, filename=name, object_key=key, sha256=digest.hexdigest(), size_bytes=size, structure=structure)
    db.add(up)
    audit.record(db, actor=me.subject, action='upload.create', entity_type='upload', entity_id=up_id,
                 after={'sha256': up.sha256, 'size': size}, details={'filename': name, 'root': structure['root']}, request=request)
    db.commit()
    return up

"""Turn any project source (git | local | zip) into an isolated working copy and an immutable snapshot.

The snapshot (tar.gz artifact + content hash) is what every later job uses, so a local folder that changes
after the scan, or a deleted upload, never alters what the plan was computed from.
"""
import hashlib
import os
import shutil
import tarfile
from pathlib import Path

from ..config import get_settings
from ..security import UnsafeInput, classify_source, safe_extract_zip, safe_join, validate_local_path
from . import gitops

EXCLUDED_DIRS = {'.git', 'target', 'node_modules', '.idea', '.mvn-cache'}
# Never copied into snapshots, candidates or reports (docs: 11-SEGURIDAD). Reported as manual actions instead.
SENSITIVE_NAMES = {'.env', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', '.npmrc', '.pgpass', 'settings-security.xml'}
SENSITIVE_SUFFIXES = ('.jks', '.p12', '.pfx', '.pem', '.key', '.keystore', '.truststore')


def is_sensitive(name: str) -> bool:
    return name in SENSITIVE_NAMES or name.startswith('.env.') or name.lower().endswith(SENSITIVE_SUFFIXES)


def sensitive_files(root: Path) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        out += [Path(dirpath, n).relative_to(root).as_posix() for n in filenames if is_sensitive(n)]
    return sorted(out)


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
        for name in sorted(filenames):
            if not is_sensitive(name):
                yield Path(dirpath, name)


def content_hash(root: Path) -> str:
    """sha256 over (relative path, executable bit, file sha256) in sorted order; ignores build output and VCS data."""
    h = hashlib.sha256()
    for f in _walk(root):
        rel = f.relative_to(root).as_posix()
        if f.is_symlink():
            h.update(f'L {rel} {os.readlink(f)}\n'.encode())
            continue
        fh = hashlib.sha256(f.read_bytes()).hexdigest()
        h.update(f'F {rel} {int(os.access(f, os.X_OK))} {fh}\n'.encode())
    return h.hexdigest()


def copy_local(src: Path, dest: Path):
    """Copy without following symlinks; links escaping the source abort the job."""
    max_bytes, total = get_settings().max_repo_mb * 1024 * 1024, 0
    src = src.resolve()
    for f in _walk(src):
        rel = f.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if f.is_symlink():
            if not f.resolve().is_relative_to(src):
                raise UnsafeInput('SYMLINK_ESCAPE', f'Enlace simbólico fuera del proyecto: {rel}')
            os.symlink(os.readlink(f), target)
            continue
        total += f.stat().st_size
        if total > max_bytes:
            raise UnsafeInput('SOURCE_TOO_LARGE', f'El proyecto supera {get_settings().max_repo_mb} MB')
        shutil.copy2(f, target)


def materialize(ctx, repo, ref, dest: Path, token=None, upload_path: Path | None = None, select: str | None = None):
    """Return (root_dir, root_rel, commit_sha_or_None, repository info). dest is created inside the job workspace.
    Doc 17: a multi-project repository is materialized as a workspace (root = workspaceRoot) unless one project is selected."""
    if repo.source_type == 'git':
        sha = gitops.checkout(ctx, repo.url, ref, dest, token)
        commit = sha
    elif repo.source_type == 'local':
        copy_local(validate_local_path(repo.local_path), dest)  # re-validated at use time
        commit = None
    elif repo.source_type == 'zip':
        if upload_path is None or not upload_path.exists():
            raise UnsafeInput('UPLOAD_MISSING', 'El archivo ZIP del proyecto ya no está disponible')
        safe_extract_zip(upload_path, dest)
        commit = None
    else:
        raise UnsafeInput('SOURCE_TYPE', f'Origen no soportado: {repo.source_type}')
    files = [p.relative_to(dest).as_posix() for p in _walk(dest) if p.name == 'pom.xml']
    from ..analysis import maven

    def modules(rel):
        try:
            return maven.read_pom(safe_join(dest, rel), rel)['modules']
        except Exception:
            return []
    info = classify_source(files, modules)
    if select:
        if select not in {p['dir'] for p in info['projects']} | {info.get('root')}:
            raise UnsafeInput('PROJECT_NOT_FOUND', f'{select} no es un proyecto detectado: {[p["dir"] for p in info["projects"]][:10]}')
        root = select
    else:
        root = info['root'] or info['workspaceRoot']
    bad = [str(p) for p in _walk(dest) if p.is_symlink() and not p.resolve().is_relative_to(dest.resolve())]
    if bad:
        raise UnsafeInput('SYMLINK_ESCAPE', f'Enlaces simbólicos fuera del proyecto: {bad[:5]}')
    return (dest if root == '.' else safe_join(dest, root)), root, commit, info


def make_snapshot(root: Path, out: Path) -> str:
    """Deterministic tar.gz of the project root (sorted, mtime 0, no owners). Returns the content hash."""
    def norm(ti: tarfile.TarInfo):
        ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = 0, 0, 0, '', ''
        return ti
    with tarfile.open(out, 'w:gz', compresslevel=6) as tar:
        for f in _walk(root):
            tar.add(f, arcname=f.relative_to(root).as_posix(), recursive=False, filter=norm)
    return content_hash(root)


def restore_snapshot(archive: Path, dest: Path, expected_hash: str):
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:gz') as tar:
        tar.extractall(dest, filter='data')  # rejects absolute paths, '..', escaping links, device files
    actual = content_hash(dest)
    if actual != expected_hash:
        raise UnsafeInput('SNAPSHOT_MISMATCH', 'El snapshot no coincide con el hash registrado')


def zip_tree(root: Path, out: Path):
    import zipfile
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in _walk(root):
            if f.is_symlink():
                continue
            info = zipfile.ZipInfo(f.relative_to(root).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))  # deterministic
            info.external_attr = (f.stat().st_mode & 0o777 | 0o100000) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, f.read_bytes())

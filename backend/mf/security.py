"""Input validation at trust boundaries: Git URLs (SSRF), secret redaction and safe paths."""
import ipaddress
import os
import re
import socket
from pathlib import Path
from urllib.parse import urlsplit

from .config import get_settings


class UnsafeInput(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _ip_is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return addr.is_global and not addr.is_multicast


def validate_git_url(url: str, resolve=socket.getaddrinfo) -> dict:
    """Return {'provider', 'url', 'host'} or raise UnsafeInput.

    Policy: https only (never file://: local folders are a separate source type), no credentials in the URL,
    host on the allowlist, and every resolved address public unless the host is declared internal.
    """
    s = get_settings()
    if len(url) > 1000 or any(c in url for c in '\r\n\t\0 '):
        raise UnsafeInput('GIT_URL_INVALID', 'URL inválida')
    parts = urlsplit(url)
    if parts.scheme != 'https':
        raise UnsafeInput('GIT_URL_SCHEME', 'Solo se admite https')
    if parts.username or parts.password or '@' in parts.netloc:
        raise UnsafeInput('GIT_URL_CREDENTIALS', 'No incluya credenciales en la URL; use una referencia de credencial')
    if parts.query or parts.fragment:
        raise UnsafeInput('GIT_URL_INVALID', 'La URL no debe tener query ni fragmento')
    host = (parts.hostname or '').lower()
    if parts.port not in (None, 443):
        raise UnsafeInput('GIT_URL_PORT', 'Puerto no permitido')
    allowed = s.list_of(s.git_allowed_hosts) + s.list_of(s.git_internal_hosts)
    if host not in allowed:
        raise UnsafeInput('GIT_HOST_NOT_ALLOWED', f'Host {host} no está en la lista permitida')
    if '..' in parts.path or not re.fullmatch(r'/[\w.\-/~]+', parts.path or ''):
        raise UnsafeInput('GIT_URL_PATH', 'Ruta de repositorio inválida')
    if host not in s.list_of(s.git_internal_hosts):
        try:
            ips = {a[4][0] for a in resolve(host, 443, proto=socket.IPPROTO_TCP)}
        except OSError:
            raise UnsafeInput('GIT_HOST_UNRESOLVED', f'No se pudo resolver {host}') from None
        if not ips or not all(_ip_is_public(ip) for ip in ips):
            raise UnsafeInput('GIT_HOST_PRIVATE', f'{host} resuelve a una dirección no pública')
    provider = {'github.com': 'github', 'gitlab.com': 'gitlab'}.get(host, 'generic')
    return {'provider': provider, 'url': f'https://{host}{parts.path}', 'host': host}


CREDENTIAL_REF = re.compile(r'^(env:MF_CRED_[A-Z0-9_]{1,100}|vault:[\w./-]{1,200})$')


def validate_credential_ref(ref: str | None):
    if ref and not CREDENTIAL_REF.match(ref):
        raise UnsafeInput('CREDENTIAL_REF_INVALID', 'Use env:MF_CRED_NOMBRE o vault:ruta; nunca el secreto')
    return ref


def resolve_credential(ref: str | None, env=None) -> str | None:
    """Only env: references are implemented; vault: requires the pending secret-manager adapter."""
    if not ref:
        return None
    if ref.startswith('env:'):
        return (env or os.environ).get(ref[4:])
    raise UnsafeInput('VAULT_NOT_CONFIGURED', 'Adaptador de gestor de secretos pendiente de configuración')


# --- Redaction ----------------------------------------------------------------------------------
_PATTERNS = [
    (re.compile(r'(?i)(authorization:\s*(?:bearer|basic|token)\s+)[^\s]+'), r'\1***'),
    (re.compile(r'(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret)([ \t]*[=:][ \t]*|">[ \t]*|=")(?!\$\{|\{\{|RAW\(\{\{|\*\*\*)([^\s"<&,;]+)'), r'\1\2***'),
    (re.compile(r'(?i)(<(password|passphrase|privateKey)>)[^<]*(</\2>)'), r'\1***\3'),
    (re.compile(r'(\w+://)[^/\s:@?#]+:[^/\s@?#]+@'), r'\1***:***@'),  # userinfo only, not ?from=a@b
    (re.compile(r'\b(ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}\b'), '***'),
    (re.compile(r'\bglpat-[A-Za-z0-9_\-]{20,}\b'), '***'),
    (re.compile(r'\bAKIA[0-9A-Z]{16}\b'), '***'),
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----'), '***PRIVATE KEY***'),
    (re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b'), '***JWT***'),
]


def redact(text: str, extra_secrets=()) -> str:
    if not text:
        return text
    for secret in extra_secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, '***')
    for rx, repl in _PATTERNS:
        text = rx.sub(repl, text)
    return text


def secret_files(root, max_bytes=1_000_000) -> list[str]:
    """Text files under root that still contain secret-looking values (names only, never values)."""
    from pathlib import Path as _P
    out = []
    for p in sorted(_P(root).rglob('*')):
        if '.git' in p.parts or 'target' in p.parts or not p.is_file() or p.is_symlink() or p.stat().st_size > max_bytes:
            continue
        if p.suffix.lower() not in ('.properties', '.yml', '.yaml', '.xml', '.java', '.json', '.conf', '.cfg', '.env', '.txt', '.sh'):
            continue
        if find_secrets(p.read_text(encoding='utf-8', errors='ignore')):
            out.append(p.relative_to(root).as_posix())
    return out


def find_secrets(text: str) -> list[str]:
    """Names of the secret patterns present (used by the G5 secret scan of a candidate diff)."""
    hits = []
    text = re.sub(r'RAW\(\{\{[^}\n]*\}\}\)|\$\{[^}\n]*\}|\{\{[^}\n]*\}\}', '', text)  # ${ENV}, {{prop}}, RAW({{prop}}) are not secrets
    for rx, _ in _PATTERNS:
        if rx.search(text):
            hits.append(rx.pattern[:40])
    return hits


# --- Paths --------------------------------------------------------------------------------------
def safe_join(base: Path, rel: str) -> Path:
    """Join a relative path under base, refusing absolute paths, traversal and symlink escapes."""
    if not rel or rel.startswith(('/', '\\')) or '\0' in rel:
        raise UnsafeInput('PATH_INVALID', 'Ruta inválida')
    base = base.resolve()
    target = (base / rel).resolve()
    if not target.is_relative_to(base):
        raise UnsafeInput('PATH_TRAVERSAL', 'Ruta fuera del directorio permitido')
    return target


def symlink_escapes(root: Path) -> list[str]:
    """Symlinks inside a checkout that point outside it (Maven would follow them)."""
    root = root.resolve()
    bad = []
    for dirpath, dirnames, filenames in os.walk(root):
        if '.git' in dirnames:
            dirnames.remove('.git')
        for name in dirnames + filenames:
            p = Path(dirpath, name)
            if p.is_symlink() and not p.resolve().is_relative_to(root):
                bad.append(str(p.relative_to(root)))
    return bad


# --- Local folders and ZIP archives ------------------------------------------------------------
import stat as _stat
import zipfile as _zipfile

IGNORED_TOP = {'__MACOSX', '.git', '.DS_Store'}


def local_roots() -> list[Path]:
    s = get_settings()
    return [Path(r.strip()).resolve() for r in s.local_source_roots.split(',') if r.strip()]  # paths are case-sensitive


def validate_local_path(raw: str) -> Path:
    """Absolute folder on the worker host, inside an authorized root, without symlink escapes."""
    roots = local_roots()
    if not roots:
        raise UnsafeInput('LOCAL_SOURCE_DISABLED', 'Carpetas locales deshabilitadas en esta instalación: use ZIP o un agente local')
    if not raw or '\0' in raw or not raw.startswith('/') or len(raw) > 1000:
        raise UnsafeInput('LOCAL_PATH_INVALID', 'Indique una ruta absoluta del equipo donde corre el worker')
    path = Path(raw).resolve()
    if not any(path == r or path.is_relative_to(r) for r in roots):
        raise UnsafeInput('LOCAL_PATH_NOT_ALLOWED', 'La ruta no está dentro de un directorio autorizado (MF_LOCAL_SOURCE_ROOTS)')
    if not path.is_dir():
        raise UnsafeInput('LOCAL_PATH_NOT_FOUND', 'La carpeta no existe en el equipo del worker')
    return path


def find_project_root(rel_files: list[str]) -> str:
    """The single Maven root (doc 17). A multi-project repository is not an error: callers that need one project get
    ROOT_SELECTION_REQUIRED; discovery callers use classify_source() instead."""
    from .workspace import RootSelectionRequired, project_root
    try:
        return project_root(rel_files)
    except RootSelectionRequired as e:
        raise UnsafeInput('ROOT_SELECTION_REQUIRED', str(e)) from None
    except ValueError:
        raise UnsafeInput('NO_POM', 'No se encontró ningún pom.xml: no parece un proyecto Maven') from None


def classify_source(rel_files: list[str], read_modules=None) -> dict:
    """SINGLE_PROJECT | MAVEN_MULTI_MODULE | MULTI_PROJECT_REPOSITORY; only a source without any pom.xml is rejected."""
    from .workspace import classify
    info = classify(rel_files, read_modules)
    if info.get('error'):
        raise UnsafeInput('NO_POM', 'No se encontró ningún pom.xml: no parece un proyecto Maven')
    return info


def inspect_zip(path: Path) -> dict:
    """Validate a ZIP without extracting: entries, sizes, ratios, names, symlinks, encryption, Maven root."""
    s = get_settings()
    try:
        zf = _zipfile.ZipFile(path)
    except (_zipfile.BadZipFile, OSError):
        raise UnsafeInput('ZIP_INVALID', 'El archivo no es un ZIP válido') from None
    with zf:
        infos = zf.infolist()
        if len(infos) > s.zip_max_entries:
            raise UnsafeInput('ZIP_TOO_MANY_ENTRIES', f'Más de {s.zip_max_entries} entradas')
        total, files = 0, []
        for i in infos:
            name = i.filename
            if i.flag_bits & 0x1:
                raise UnsafeInput('ZIP_ENCRYPTED', 'ZIP cifrado no admitido')
            if '\\' in name or name.startswith('/') or '\0' in name or ':' in name.split('/')[0] \
                    or any(part == '..' for part in name.split('/')):
                raise UnsafeInput('ZIP_PATH_TRAVERSAL', f'Entrada con ruta no permitida: {name[:120]}')
            mode = i.external_attr >> 16
            if _stat.S_ISLNK(mode):
                raise UnsafeInput('ZIP_SYMLINK', f'Enlaces simbólicos no admitidos: {name[:120]}')
            if i.is_dir():
                continue
            total += i.file_size
            if i.compress_size and i.file_size / i.compress_size > s.zip_max_ratio and i.file_size > 1_000_000:
                raise UnsafeInput('ZIP_BOMB', f'Relación de compresión excesiva en {name[:120]}')
            if total > s.zip_max_uncompressed_mb * 1024 * 1024:
                raise UnsafeInput('ZIP_TOO_LARGE', f'Supera {s.zip_max_uncompressed_mb} MB descomprimido')
            if name.split('/')[0] not in IGNORED_TOP:
                files.append(name)
        info = classify_source(files)
        root = info['root'] or info['workspaceRoot']
        prefix = '' if root == '.' else root + '/'
        modules = sorted(f[len(prefix):] for f in files if f.startswith(prefix) and (f == prefix + 'pom.xml' or f.endswith('/pom.xml')))
        return {'root': root, 'modules': modules, 'entries': len(infos), 'uncompressedBytes': total,
                'repositoryType': info['repositoryType'], 'projects': [p['name'] for p in info['projects']]}


def safe_extract_zip(path: Path, dest: Path) -> str:
    """Extract after inspect_zip; every target re-checked under dest. Returns the project root (relative)."""
    info = inspect_zip(path)
    dest.mkdir(parents=True, exist_ok=True)
    base = dest.resolve()
    with _zipfile.ZipFile(path) as zf:
        written = 0
        for i in zf.infolist():
            if i.filename.split('/')[0] in IGNORED_TOP:
                continue
            target = (base / i.filename).resolve()
            if not target.is_relative_to(base):
                raise UnsafeInput('ZIP_PATH_TRAVERSAL', 'Ruta fuera del destino')
            if i.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(i) as src, open(target, 'wb') as out:
                while chunk := src.read(1 << 20):  # count real bytes, not declared sizes
                    written += len(chunk)
                    if written > get_settings().zip_max_uncompressed_mb * 1024 * 1024:
                        raise UnsafeInput('ZIP_TOO_LARGE', 'Tamaño descomprimido real excesivo')
                    out.write(chunk)
            if (i.external_attr >> 16) & 0o111:
                target.chmod(0o755)
    return info['root']

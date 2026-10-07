"""Result artifact (19-CORRECCION part B §14-§19): the final validated candidate as a ZIP with a `.migration/` folder
(manifest.json, migration-report.md, manual-actions.md, diff.patch). Only the final candidate is packaged, never the
original source nor an intermediate candidate; target/, .git, IDE caches and work directories are excluded."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

EXCLUDED_DIRS = {'target', '.git', '.idea', '.vscode', '.settings', '.work', '.migration-tmp', 'node_modules', '.m2-legacy', '.m2-migrated'}
EXCLUDED_FILES = {'.DS_Store', '.classpath', '.project'}
EXCLUDED_SUFFIXES = ('.iml',)
SOURCE_TYPE = {'local': 'LOCAL', 'zip': 'LOCAL', 'git': 'GIT_REMOTE'}  # CONNECTED_REPOSITORY: no connector exists yet


def source_type(raw: str) -> str:
    return SOURCE_TYPE.get(raw, 'LOCAL')


def download_label(status: str | None) -> dict:
    """§16: wording depends on the real status; a FAILED candidate is never called "corregido"."""
    s = (status or '').upper()
    if s == 'SUCCEEDED':
        return {'label': 'Descargar proyecto migrado', 'kind': 'project', 'warning': None}
    if s == 'PARTIAL_SUCCESS':
        return {'label': 'Descargar proyecto migrado con pendientes', 'kind': 'project', 'warning': 'Existen acciones manuales pendientes.'}
    if s == 'BLOCKED':
        return {'label': 'Descargar análisis', 'kind': 'analysis', 'warning': 'La migración se bloqueó: no hay proyecto migrado; descargue el análisis y las acciones requeridas.'}
    return {'label': 'Descargar candidate para diagnóstico', 'kind': 'diagnostic',
            'warning': 'La migración falló: el candidate sirve para diagnóstico, no es un proyecto corregido.'}


def manifest(*, status, source, target: dict, projects: int, build_passed, tests_passed, manual_actions: int, extra=None) -> dict:
    return {'status': status, 'sourceType': source_type(source) if source.islower() else source, 'target': target, 'projects': projects,
            'buildPassed': bool(build_passed), 'testsPassed': bool(tests_passed), 'manualActions': manual_actions,
            'download': download_label(status)} | (extra or {})


def _included(rel: Path) -> bool:
    return not (set(rel.parts[:-1]) & EXCLUDED_DIRS or rel.name in EXCLUDED_FILES or rel.name.endswith(EXCLUDED_SUFFIXES)
                or rel.parts[0] in EXCLUDED_DIRS)


def build_zip(tree: Path, out: Path | None, man: dict, docs: dict[str, str], prefix: str = '') -> bytes:
    """Deterministic ZIP of `tree` (relative paths preserved under `prefix`) + `.migration/`; written to `out` if given."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        def put(name, data: bytes):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type, info.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
            z.writestr(info, data)
        for f in sorted(tree.rglob('*')):
            rel = f.relative_to(tree)
            if f.is_file() and not f.is_symlink() and _included(rel):
                put(f'{prefix}{rel.as_posix()}', f.read_bytes())
        put('.migration/manifest.json', json.dumps(man, ensure_ascii=False, indent=2).encode())
        for name, text in sorted(docs.items()):
            if text:
                put(f'.migration/{name}', text.encode())
    data = buf.getvalue()
    if out:
        out.write_bytes(data)
    return data


if __name__ == '__main__':  # self-check
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        t = Path(d) / 'p'
        for rel in ('pom.xml', 'src/main/java/A.java', 'target/x.class', '.git/HEAD', '.idea/w.xml', 'p.iml', 'docs/README.md'):
            (t / rel).parent.mkdir(parents=True, exist_ok=True)
            (t / rel).write_text('x')
        m = manifest(status='PARTIAL_SUCCESS', source='local', target={'java': '21'}, projects=1, build_passed=True, tests_passed=True, manual_actions=2)
        z = zipfile.ZipFile(io.BytesIO(build_zip(t, None, m, {'diff.patch': 'diff --git', 'manual-actions.md': '# x'})))
        names = sorted(z.namelist())
        assert names == ['.migration/diff.patch', '.migration/manifest.json', '.migration/manual-actions.md', 'docs/README.md', 'pom.xml', 'src/main/java/A.java'], names
        assert json.loads(z.read('.migration/manifest.json'))['sourceType'] == 'LOCAL'
        assert build_zip(t, None, m, {}) == build_zip(t, None, m, {})  # deterministic
    assert download_label('FAILED')['label'] == 'Descargar candidate para diagnóstico'
    assert download_label('SUCCEEDED')['label'] == 'Descargar proyecto migrado'
    print('ok')

"""Degraded mode (32-CORRECCION §13): placeholder POM + empty jar for low-criticality external artifacts that cannot be
resolved, written into the run's isolated local repository so OpenRewrite can parse the POM and run the recipes.

They exist only in the work directory, are never compiled against for validation (the build is reported as
NOT_EXECUTABLE) and are never part of the delivered project."""
from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

MARKER = '_migration-factory.stub'  # in every stub version directory: what StubLeakGuard and remove_stubs look for
STUB_TEXT = 'migration-factory stub'


def _dir(repo: Path, coord: str) -> Path | None:
    parts = coord.split(':')
    if len(parts) not in (4, 5):
        return None
    g, a, v = parts[0], parts[1], parts[-1]
    return repo / Path(*g.split('.')) / a / v


def install_stubs(repo: Path, coords: list[str]) -> list[str]:
    done = []
    for c in coords:
        parts = c.split(':')
        if len(parts) == 4:
            g, a, t, v = parts
        elif len(parts) == 5:
            g, a, t, _cls, v = parts
        else:
            continue
        d = repo / Path(*g.split('.')) / a / v
        d.mkdir(parents=True, exist_ok=True)
        (d / f'{a}-{v}.pom').write_text(f'<project><modelVersion>4.0.0</modelVersion><groupId>{g}</groupId><artifactId>{a}</artifactId>'
                                        f'<version>{v}</version><packaging>{t if t != "pom" else "pom"}</packaging>'
                                        f'<description>migration-factory stub (modo degradado; no es el artefacto real)</description></project>')
        if t != 'pom':
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as z:
                z.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\nCreated-By: migration-factory stub\n')
            (d / f'{a}-{v}.{t}').write_bytes(buf.getvalue())
        # empty repository id = installed locally: usable whatever repository Maven asks for
        (d / '_remote.repositories').write_text(f'{a}-{v}.pom>=\n' + (f'{a}-{v}.{t}>=\n' if t != 'pom' else ''))
        (d / MARKER).write_text(c + '\n')
        done.append(c)
    return done


def stub_present(repo: Path, coord: str) -> bool:
    d = _dir(Path(repo), coord)
    return bool(d and (d / MARKER).exists())


def remove_stubs(repo: Path, coords: list[str]) -> list[str]:
    """code-review 45/46: once the recipes ran, the stubs leave the local repository so compile/test/package resolve for real.
    Only directories carrying the stub marker are removed (a real artifact downloaded later is never touched)."""
    removed = []
    for c in coords:
        d = _dir(Path(repo), c)
        if d and (d / MARKER).exists():
            shutil.rmtree(d, ignore_errors=True)
            removed.append(c)
    return removed


if __name__ == '__main__':  # self-check
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        assert install_stubs(Path(d), ['com.oracle:ojdbc6:jar:11.2.0.4']) == ['com.oracle:ojdbc6:jar:11.2.0.4']
        base = Path(d) / 'com/oracle/ojdbc6/11.2.0.4'
        assert (base / 'ojdbc6-11.2.0.4.pom').exists() and zipfile.ZipFile(base / 'ojdbc6-11.2.0.4.jar').namelist() == ['META-INF/MANIFEST.MF']
        assert stub_present(Path(d), 'com.oracle:ojdbc6:jar:11.2.0.4')
        assert remove_stubs(Path(d), ['com.oracle:ojdbc6:jar:11.2.0.4']) == ['com.oracle:ojdbc6:jar:11.2.0.4'] and not base.exists()
    print('ok')

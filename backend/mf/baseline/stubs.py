"""Degraded mode (32-CORRECCION §13): placeholder POM + empty jar for low-criticality external artifacts that cannot be
resolved, written into the run's isolated local repository so OpenRewrite can parse the POM and run the recipes.

They exist only in the work directory, are never compiled against for validation (the build is reported as
NOT_EXECUTABLE) and are never part of the delivered project."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path


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
        done.append(c)
    return done


if __name__ == '__main__':  # self-check
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        assert install_stubs(Path(d), ['com.oracle:ojdbc6:jar:11.2.0.4']) == ['com.oracle:ojdbc6:jar:11.2.0.4']
        base = Path(d) / 'com/oracle/ojdbc6/11.2.0.4'
        assert (base / 'ojdbc6-11.2.0.4.pom').exists() and zipfile.ZipFile(base / 'ojdbc6-11.2.0.4.jar').namelist() == ['META-INF/MANIFEST.MF']
    print('ok')

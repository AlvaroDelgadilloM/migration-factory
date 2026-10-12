"""StubLeakGuard (code-review 46): degraded-mode stubs may serve OpenRewrite, never the final build or the deliverable.

Runs before candidate compile/test and before packaging. FAIL (STUB_LEAK_DETECTED) blocks them, with evidence:
- the candidate tree holds no `.m2-degraded` directory nor stub marker/jar;
- no candidate POM points to a stub repository or carries the stub description;
- the repositories Maven will use hold no stub of the stubbed coordinates (final classpath cannot use one);
- the degraded overlay is no longer enabled.
Stdlib only."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from ..baseline.stubs import MARKER, STUB_TEXT, stub_present

SKIP = {'.git', 'target', 'node_modules'}
STUB_REPO = re.compile(r'<url>\s*file:[^<]*\.m2-degraded', re.I)


def _files(root: Path):
    for p in root.rglob('*'):
        if not any(x in SKIP for x in p.relative_to(root).parts):
            yield p


def check(candidate: Path, stubbed: list[str], repos=(), overlay_enabled: bool = False) -> dict:
    candidate = Path(candidate)
    leaks = []
    contains_repo = False
    for p in _files(candidate) if candidate.exists() else ():
        rel = p.relative_to(candidate).as_posix()
        if p.name == '.m2-degraded' or p.name == MARKER:
            leaks.append({'kind': 'STUB_FILE_IN_CANDIDATE', 'path': rel})
        elif p.name == 'pom.xml':
            text = p.read_text(encoding='utf-8', errors='replace')
            if STUB_REPO.search(text):
                contains_repo = True
                leaks.append({'kind': 'STUB_REPOSITORY_IN_POM', 'path': rel})
            if STUB_TEXT in text:
                leaks.append({'kind': 'STUB_ARTIFACT_IN_POM', 'path': rel})
        elif p.suffix == '.jar' and p.stat().st_size < 4096:  # stub jars are an empty manifest
            try:
                with zipfile.ZipFile(p) as z:
                    if STUB_TEXT in z.read('META-INF/MANIFEST.MF').decode(errors='replace'):
                        leaks.append({'kind': 'STUB_JAR_IN_CANDIDATE', 'path': rel})
            except (zipfile.BadZipFile, KeyError, OSError):
                pass
    on_classpath = sorted({c for r in repos if r for c in stubbed if stub_present(Path(r), c)})
    leaks += [{'kind': 'STUB_IN_MAVEN_REPOSITORY', 'artifact': c} for c in on_classpath]
    if overlay_enabled:
        leaks.append({'kind': 'DEGRADED_OVERLAY_ENABLED'})
    return {'name': 'STUB_LEAK_GUARD', 'status': 'FAIL' if leaks else 'PASS', 'code': 'STUB_LEAK_DETECTED' if leaks else None,
            'evidence': {'stubbedDependencies': list(stubbed), 'overlayEnabled': overlay_enabled, 'candidateContainsStubRepo': contains_repo,
                         'finalClasspathContainsStub': bool(on_classpath)},
            'leaks': leaks[:50]}


if __name__ == '__main__':  # self-check
    import tempfile
    from ..baseline.stubs import install_stubs, remove_stubs
    c = 'com.oracle:ojdbc6:jar:11.2.0.4'
    with tempfile.TemporaryDirectory() as d:
        cand, repo = Path(d) / 'cand', Path(d) / 'repo'
        cand.mkdir()
        (cand / 'pom.xml').write_text('<project><dependencies><dependency><groupId>com.oracle</groupId><artifactId>ojdbc6</artifactId>'
                                      '<version>11.2.0.4</version></dependency></dependencies></project>')
        install_stubs(repo, [c])
        r = check(cand, [c], [repo], overlay_enabled=True)
        assert r['status'] == 'FAIL' and r['evidence']['finalClasspathContainsStub'] and {x['kind'] for x in r['leaks']} == {
            'STUB_IN_MAVEN_REPOSITORY', 'DEGRADED_OVERLAY_ENABLED'}
        remove_stubs(repo, [c])
        assert check(cand, [c], [repo])['status'] == 'PASS'          # the real coordinate in the POM is not a leak
        install_stubs(cand / '.m2-degraded', [c])
        assert {x['kind'] for x in check(cand, [c])['leaks']} >= {'STUB_FILE_IN_CANDIDATE', 'STUB_JAR_IN_CANDIDATE'}
    print('ok')

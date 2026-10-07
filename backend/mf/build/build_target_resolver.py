"""BuildTargetResolver (21-CORRECCION): what Maven may build. The workspace coordinates, the ProjectContext compiles.

    SINGLE_PROJECT            -> PROJECT (its own pom.xml)
    MAVEN_MULTI_MODULE        -> WORKSPACE_AGGREGATOR (the root pom.xml with <modules>)
    MULTI_PROJECT_REPOSITORY  -> NOT_APPLICABLE: never `workspaceRoot/pom.xml`; every project is built on its own

Stdlib only. No aggregator POM is ever created to make the workspace buildable."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

NOT_APPLICABLE = 'WORKSPACE_BUILD_NOT_APPLICABLE'


@dataclass(frozen=True)
class BuildTarget:
    type: str                 # PROJECT | WORKSPACE_AGGREGATOR | NOT_APPLICABLE | INVALID
    pom: Path | None
    reason: str | None = None

    @property
    def buildable(self) -> bool:
        return self.type in ('PROJECT', 'WORKSPACE_AGGREGATOR') and self.pom is not None


def for_workspace(info: dict, workspace_root: Path) -> BuildTarget:
    """info: workspace.classify() result; workspace_root: the directory info['root'/'workspaceRoot'] points to."""
    kind = info.get('repositoryType')
    if kind == 'MULTI_PROJECT_REPOSITORY':
        return BuildTarget('NOT_APPLICABLE', None, NOT_APPLICABLE)
    if kind in ('SINGLE_PROJECT', 'MAVEN_MULTI_MODULE'):
        pom = workspace_root / 'pom.xml'
        if pom.is_file():
            return BuildTarget('PROJECT' if kind == 'SINGLE_PROJECT' else 'WORKSPACE_AGGREGATOR', pom)
        return BuildTarget('INVALID', None, 'MISSING_PROJECT_POM')
    return BuildTarget('INVALID', None, 'UNKNOWN_REPOSITORY_TYPE')


def for_project(project_root: Path) -> BuildTarget:
    """A ProjectContext (baseline or candidate root)."""
    if not project_root.is_dir():
        return BuildTarget('INVALID', None, 'MISSING_CANDIDATE_DIRECTORY')
    pom = project_root / 'pom.xml'
    if not pom.is_file():
        return BuildTarget('INVALID', None, 'MISSING_CANDIDATE_POM')
    return BuildTarget('PROJECT', pom)


def workspace_build_status(info: dict) -> dict:
    """Reported, never an error: a workspace without an aggregator has no workspace build."""
    if info.get('repositoryType') == 'MULTI_PROJECT_REPOSITORY':
        return {'status': 'SKIPPED', 'reason': NOT_APPLICABLE, 'aggregatorPom': 'NOT PRESENT'}
    return {'status': 'APPLICABLE', 'reason': None, 'aggregatorPom': 'PRESENT' if info.get('aggregator') else 'NOT PRESENT'}


if __name__ == '__main__':  # self-check: cases 1, 3, 5
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d) / 'develop'
        (ws / 'commons' / 'a').mkdir(parents=True)
        (ws / 'commons' / 'a' / 'pom.xml').write_text('<project/>')
        t = for_workspace({'repositoryType': 'MULTI_PROJECT_REPOSITORY'}, ws)
        assert t.type == 'NOT_APPLICABLE' and t.pom is None and not t.buildable and t.reason == NOT_APPLICABLE  # never ws/pom.xml
        assert for_project(ws / 'commons' / 'a').pom == ws / 'commons' / 'a' / 'pom.xml'
        assert for_project(ws / 'commons' / 'b').reason == 'MISSING_CANDIDATE_DIRECTORY'
        (ws / 'commons' / 'b').mkdir()
        assert for_project(ws / 'commons' / 'b').reason == 'MISSING_CANDIDATE_POM'
        assert workspace_build_status({'repositoryType': 'MULTI_PROJECT_REPOSITORY'})['status'] == 'SKIPPED'
        (ws / 'pom.xml').write_text('<project/>')
        assert for_workspace({'repositoryType': 'MAVEN_MULTI_MODULE'}, ws).type == 'WORKSPACE_AGGREGATOR'
    print('ok')

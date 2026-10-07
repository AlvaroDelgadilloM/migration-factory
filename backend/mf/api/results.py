"""Result of an execution and how it can be integrated (19-CORRECCION part B): ZIP download always, PR only via the gate."""
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, gates
from ..auth import Principal, current_principal
from ..db import get_db
from ..errors import ApiError
from ..integration.adapters import integrations, pr_capability
from ..models import Artifact, Project, Repository
from ..output.result import download_label, source_type
from ..storage import ArtifactStore
from .executions import load_execution

router = APIRouter(tags=['results'])
NAMES = {'candidate-zip': 'candidateZip', 'report-html': 'report', 'patch': 'diff', 'report-md': None}


@router.get('/executions/{execution_id}/result')
def execution_result(execution_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    ex = load_execution(db, me, execution_id)
    project = db.get(Project, ex.project_id)
    repo = db.get(Repository, project.repository_id)
    st = source_type(repo.source_type)
    arts = {a.name: str(a.id) for a in db.scalars(select(Artifact).where(Artifact.execution_id == ex.id))}
    status = (ex.summary or {}).get('status') or ex.state.upper()
    pr = pr_capability(st, repo.provider, project.pr_config, gates.pr_blockers(db, ex) if ex.mode == 'apply' else ['Solo ejecuciones apply'])
    return {'executionId': str(ex.id), 'sourceType': st, 'status': status, 'download': download_label(status) | {'available': 'candidate.zip' in arts},
            'artifacts': {'candidateZip': arts.get('candidate.zip'), 'report': arts.get('execution-report.html'), 'reportJson': arts.get('execution-report.json'),
                          'diff': arts.get('candidate.patch'), 'manualActions': arts.get('manual-actions.md'),
                          'camelComponents': arts.get('camel-component-migration-report.md')},
            'pullRequestAvailable': pr['available'], 'pullRequest': pr, 'integrations': integrations(st, pr)}


@router.get('/executions/{execution_id}/download')
def download_result(execution_id: uuid.UUID, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    ex = load_execution(db, me, execution_id)
    art = db.scalar(select(Artifact).where(Artifact.execution_id == ex.id, Artifact.kind == 'candidate-zip').order_by(Artifact.created_at.desc()))
    if art is None:
        raise ApiError(404, 'NOT_FOUND', 'La ejecución no tiene proyecto resultante (bloqueada o sin cambios)')
    try:
        data = ArtifactStore().read(art)
    except (OSError, ValueError):
        raise ApiError(410, 'ARTIFACT_UNAVAILABLE', 'Artefacto no disponible o íntegro') from None
    status = (ex.summary or {}).get('status') or ex.state.upper()
    name = 'migrated-project.zip' if status in ('SUCCEEDED', 'PARTIAL_SUCCESS') else 'candidate-diagnostico.zip'
    audit.record(db, actor=me.subject, action='result.download', entity_type='execution', entity_id=ex.id, project_id=ex.project_id,
                 details={'name': name, 'sha256': art.sha256, 'status': status}, request=request)
    db.commit()
    return Response(data, media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="{name}"',
                                                                  'X-Content-SHA256': art.sha256})

"""Change ledger of an execution and modernization of its candidate (12-PROMPT-CLAUDE-MODERNIZATION).
Long work runs in the worker; nothing here runs Maven."""
import json
import uuid

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, jobs
from ..auth import Principal, authorize, current_principal
from ..db import get_db
from ..errors import ApiError
from ..models import Artifact, Job, Modernization
from ..schemas import JobOut, ModernizationAccepted, ModernizationIn, ModernizationOut
from ..storage import ArtifactStore
from .executions import load_execution

router = APIRouter(tags=['modernization'])


def _out(db, mo: Modernization) -> dict:
    d = ModernizationOut.model_validate(mo).model_dump(mode='json')
    job = db.get(Job, mo.job_id) if mo.job_id else None
    d['job'] = JobOut.model_validate(job).model_dump(mode='json') if job else None
    return d


@router.get('/executions/{execution_id}/changes', response_model=list[dict])
def changes(execution_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    """Per-step change record: rule, file, lines, before/after (redacted), confidence level, snapshot and rollback patch."""
    ex = load_execution(db, me, execution_id)
    art = db.scalar(select(Artifact).where(Artifact.execution_id == ex.id, Artifact.kind == 'changes-json').order_by(Artifact.created_at.desc()))
    if art is None:
        return []
    try:
        return json.loads(ArtifactStore().read(art))
    except (OSError, ValueError):
        raise ApiError(410, 'ARTIFACT_UNAVAILABLE', 'Registro de cambios no disponible o íntegro') from None


@router.get('/modernization-rules')
def modernization_rules(me: Principal = Depends(current_principal)):
    from ..modernization.detect import load_rules
    return [{k: r[k] for k in ('id', 'tier', 'level', 'publicContract', 'mechanism', 'title', 'rationale')} for r in load_rules()['rules']]


@router.post('/executions/{execution_id}/modernizations', status_code=202, response_model=ModernizationAccepted)
def start(execution_id: uuid.UUID, body: ModernizationIn, request: Request, idempotency_key: str | None = Header(None),
          db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    from ..modernization.detect import load_rules
    ex = load_execution(db, me, execution_id, 'execute')
    payload = body.model_dump(mode='json')
    endpoint = f'POST /executions/{execution_id}/modernizations'
    replay = jobs.idempotent_replay(db, me.subject, idempotency_key, endpoint, payload)
    if replay:
        return replay
    rules = load_rules()['byId']
    bad = [r for r in body.approve if r not in rules or rules[r]['mechanism'] == 'proposal']
    if bad:  # ARCHITECTURE and proposals are never applied, whatever is approved
        raise ApiError(422, 'VALIDATION_ERROR', 'Solo se aprueban reglas con transformación', {'rules': bad})
    if any(rules[r]['publicContract'] for r in body.approve):
        authorize(db, me, ex.project_id, 'plan')  # changing public contracts is an architect/owner decision
    if ex.mode != 'apply' or ex.state not in ('succeeded', 'partial_success'):
        raise ApiError(409, 'INVALID_STATE', 'Se moderniza el candidato de una ejecución apply terminada')
    if db.scalar(select(Artifact.id).where(Artifact.execution_id == ex.id, Artifact.kind == 'candidate-zip')) is None:
        raise ApiError(409, 'INVALID_STATE', 'La ejecución no tiene candidate.zip')
    if db.scalar(select(Modernization.id).where(Modernization.execution_id == ex.id, Modernization.state.in_(('queued', 'running')))):
        raise ApiError(409, 'INVALID_STATE', 'Ya hay una modernización en curso para esta ejecución')
    mo = Modernization(project_id=ex.project_id, execution_id=ex.id, approved=sorted(set(body.approve)), created_by=me.subject)
    db.add(mo)
    db.flush()
    job = jobs.create_job(db, kind='modernize', project_id=ex.project_id, subject_id=mo.id, actor=me.subject)
    mo.job_id = job.id
    result = {'modernizationId': str(mo.id), 'jobId': str(job.id), 'status': 'queued'}
    jobs.remember(db, me.subject, idempotency_key, endpoint, payload, 202, result)
    audit.record(db, actor=me.subject, action='modernization.request', entity_type='execution', entity_id=ex.id, project_id=ex.project_id,
                 details={'approved': mo.approved}, request=request)
    db.commit()
    jobs.try_publish(db)
    return result


@router.get('/executions/{execution_id}/modernizations', response_model=list[ModernizationOut])
def list_for_execution(execution_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    ex = load_execution(db, me, execution_id)
    return [_out(db, m) for m in db.scalars(select(Modernization).where(Modernization.execution_id == ex.id)
                                             .order_by(Modernization.created_at.desc()))]


@router.get('/modernizations/{modernization_id}', response_model=ModernizationOut)
def get_one(modernization_id: uuid.UUID, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    mo = db.get(Modernization, modernization_id)
    if mo is None:
        raise ApiError(404, 'NOT_FOUND', 'Recurso no encontrado')
    authorize(db, me, mo.project_id, 'view')
    return _out(db, mo)

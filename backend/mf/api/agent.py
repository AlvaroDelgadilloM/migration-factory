"""Local agent (source type `agent`): the code stays on the user's machine. `migrate.py agent push` runs the same analysis
engine there and sends the FACTS it found (modules, routes, rule hits with file and line); the platform stores them as a scan
so Inventario, Hallazgos and Plan work as for any project.

The agent is untrusted input: sizes and shapes are bounded, every finding must name a rule of the platform's active catalog,
and its severity, classification, blocking flag, recommendation and fingerprint are taken from that catalog, never from the
agent; evidence is redacted again. Executions need the source and run with the CLI on the agent's machine."""
from __future__ import annotations

import json
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Principal, authorize, current_principal
from ..db import get_db
from ..errors import ApiError
from ..models import Project, Repository, RuleCatalog, Scan, now
from ..schemas import Dto

router = APIRouter(tags=['agent'])
MAX_BODY = 20 * 1024 * 1024
Str = Annotated[str, Field(max_length=2000)]


class AgentModule(Dto):
    file: Str
    group_id: Str | None = None
    artifact_id: Str | None = None
    version: Str | None = None
    packaging: Str | None = None
    java_observed: Str | None = None
    java_resolved: Str | None = None
    java_source: Str | None = None
    camel_versions: list[Str] = Field(default_factory=list, max_length=50)
    resolution: Str | None = None
    unresolved: list[Str] = Field(default_factory=list, max_length=500)
    static_unresolved: list[Str] = Field(default_factory=list, max_length=500)
    dependencies: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)
    profiles: list[Any] = Field(default_factory=list, max_length=200)


class AgentEndpoint(Dto):
    component: Str | None = None
    uri: Str | None = None
    dynamic: bool = False
    kind: Str | None = None
    line: int | None = None


class AgentRoute(Dto):
    key: Str
    route_id: Str | None = None
    dsl: Str
    file: Str
    line: int | None = None
    detection: Str
    from_uri: Str | None = None
    dynamic_from: bool = False
    endpoints: list[AgentEndpoint] = Field(default_factory=list, max_length=500)


class AgentFinding(Dto):
    rule_id: Annotated[str, Field(pattern=r'^[A-Z0-9_]{2,60}$')]
    file: Str
    line: int = Field(ge=0)
    evidence: Annotated[str, Field(max_length=500)] = ''


class AgentReport(Dto):
    modules: list[AgentModule] = Field(max_length=2000)
    routes: list[AgentRoute] = Field(default_factory=list, max_length=20000)
    findings: list[AgentFinding] = Field(default_factory=list, max_length=50000)
    components: list[Str] = Field(default_factory=list, max_length=500)
    route_builders: list[Any] = Field(default_factory=list, max_length=5000)
    summary: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list, max_length=5000)


class AgentScanIn(Dto):
    target: Literal['spring', 'quarkus'] = 'spring'
    snapshot_hash: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    project_root: Annotated[str, Field(max_length=500)] = '.'
    maven_resolution: Literal['maven-effective', 'failed', 'skipped'] = 'skipped'
    agent_version: Annotated[str, Field(max_length=50)] = ''
    report: AgentReport


@router.post('/projects/{project_id}/agent-scans', status_code=201)
async def agent_scan(project_id: uuid.UUID, request: Request, db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise ApiError(413, 'PAYLOAD_TOO_LARGE', f'Máximo {MAX_BODY // (1024 * 1024)} MB')
    try:
        body = AgentScanIn.model_validate_json(raw)
    except ValueError as e:
        raise ApiError(422, 'VALIDATION_ERROR', f'Reporte del agente inválido: {str(e)[:500]}') from None
    p: Project = authorize(db, me, project_id, 'execute')
    if p.migration_type != 'JAVA_CAMEL' or db.get(Repository, p.repository_id).source_type != 'agent':
        raise ApiError(409, 'NOT_AN_AGENT_PROJECT', 'Solo un proyecto con origen «agente local» recibe análisis del agente')
    catalog = db.scalar(select(RuleCatalog).where(RuleCatalog.status == 'active').order_by(RuleCatalog.created_at.desc()))
    rules = {r['id']: r for r in catalog.rules}
    unknown = sorted({f.rule_id for f in body.report.findings} - set(rules))
    if unknown:
        raise ApiError(422, 'UNKNOWN_RULES', f'Reglas que este catálogo ({catalog.version}) no tiene: {", ".join(unknown[:10])}; '
                                             'actualice el agente o el catálogo')
    from ..analysis.scanner import _finding
    from ..security import redact
    from ..worker.tasks import persist_rows
    report = body.report.model_dump(by_alias=True)
    findings = {}
    for f in body.report.findings:  # catalog metadata, never the agent's
        x = _finding(rules[f.rule_id], f.file, f.line, redact(f.evidence))
        findings.setdefault(x['fingerprint'], x)
    report['findings'] = list(findings.values())
    report['summary'] = dict(report['summary']) | {'findings': len(findings), 'agent': {'version': body.agent_version, 'by': me.subject}}
    scan = Scan(project_id=p.id, ref='agent', catalog_id=catalog.id, target=body.target, created_by=me.subject, status='succeeded',
                project_root=body.project_root, snapshot_hash=body.snapshot_hash, maven_resolution=body.maven_resolution,
                maven_resolution_detail='agente local: Maven corrió en la máquina del usuario; el código no se subió', started_at=now(), finished_at=now())
    db.add(scan)
    db.flush()
    persist_rows(db, scan, report)
    scan.summary, scan.errors, scan.components = report['summary'], report['errors'][:5000], report['components']
    scan.route_builders = report['routeBuilders']
    audit.record(db, actor=me.subject, action='scan.agent', entity_type='scan', entity_id=scan.id, project_id=p.id,
                 details={'snapshotHash': body.snapshot_hash, 'findings': len(findings), 'modules': len(report['modules']),
                          'catalog': catalog.version, 'agentVersion': body.agent_version}, request=request)
    db.commit()
    return {'scanId': str(scan.id), 'findings': len(findings), 'catalog': catalog.version}


if __name__ == '__main__':  # the report the CLI sends must validate
    print(json.dumps(AgentScanIn.model_json_schema()['required']))

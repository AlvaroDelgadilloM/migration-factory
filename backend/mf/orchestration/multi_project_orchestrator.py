"""MultiProjectOrchestrator (18-CORRECCION): when repositoryType = MULTI_PROJECT_REPOSITORY every ProjectContext is
analysed (in parallel, configurable), results are kept per project, one failing project never aborts the others, and the
workspace gets an aggregated status, shared findings, dependency graph, order and waves.

Analysis order != migration order: analysis covers all projects; migration follows the internal dependency graph."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import workspace
from .analysis_wave_orchestrator import OUTCOME

PROJECT_STATES = ('PENDING', 'RUNNING', 'PASS', 'WARN', 'PARTIAL', 'FAIL', 'BLOCKED', 'SKIPPED')


@dataclass
class ProjectContext:
    name: str
    root: Path
    pom: Path
    group_id: str | None = None
    artifact_id: str | None = None
    version: str | None = None
    packaging: str | None = None
    technologies: list = field(default_factory=list)
    integrations: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)  # internal projects this one depends on
    analysis_status: str = 'PENDING'
    migration_status: str = 'PENDING'
    error: str | None = None
    report: dict | None = None
    dependency_context: list = field(default_factory=list)  # findings inherited from internal dependencies (doc 20 §6)
    context_missing: list = field(default_factory=list)     # dependencies whose analysis is missing -> PARTIAL

    def summary(self) -> dict:
        return {'project': self.name, 'dir': str(self.root), 'groupId': self.group_id, 'artifactId': self.artifact_id, 'version': self.version,
                'packaging': self.packaging, 'technologies': self.technologies, 'integrations': len(self.integrations),
                'findings': len(self.findings), 'dependencies': self.dependencies, 'analysisStatus': self.analysis_status,
                'analysis': OUTCOME.get(self.analysis_status, self.analysis_status), 'contextMissing': self.context_missing,
                'migrationStatus': self.migration_status, 'error': self.error}


@dataclass
class WorkspaceContext:
    workspace_root: Path
    info: dict
    projects: list[ProjectContext]
    dependency_graph: dict
    order: dict
    shared_findings: dict | None = None
    analysis_status: str = 'PENDING'
    migration_status: str = 'PENDING'
    analysis_waves: list = field(default_factory=list)
    migration_waves: list = field(default_factory=list)


def build_context(source: Path) -> WorkspaceContext:
    """DISCOVER WORKSPACE -> DISCOVER PROJECTS -> BUILD PROJECT CONTEXTS -> BUILD INTERNAL DEPENDENCY GRAPH (all projects)."""
    from ..analysis import maven
    info = workspace.classify_dir(source)
    if info.get('error'):
        raise ValueError('NO_POM')
    ws = source if info['workspaceRoot'] == '.' else source / info['workspaceRoot']
    rel = lambda d: d if info['workspaceRoot'] == '.' else d[len(info['workspaceRoot']) + 1:]
    raw = [p | {'dir': rel(p['dir'])} for p in info['projects']]
    graph = workspace.dependency_graph(ws, raw)
    deps = {}
    for e in graph['edges']:
        deps.setdefault(e['from'], []).append(e['to'])
    projects = []
    for p in raw:
        root = ws if p['dir'] == '.' else ws / p['dir']
        ctx = ProjectContext(name=p['name'], root=root, pom=root / 'pom.xml', dependencies=sorted(deps.get(p['name'], [])))
        try:
            pom = maven.read_pom(ctx.pom, 'pom.xml')
            ctx.group_id, ctx.artifact_id, ctx.version, ctx.packaging = pom['groupId'], pom['artifactId'], pom['version'], pom['packaging']
        except Exception as e:  # unreadable POM: the project is still listed and its analysis will report it
            ctx.error = f'POM ilegible: {type(e).__name__}'
        projects.append(ctx)
    return WorkspaceContext(workspace_root=ws, info=info, projects=projects, dependency_graph=graph, order=workspace.plan_order(graph))


def _technologies(r: dict) -> list[str]:
    rules = {f['ruleId'] for f in r.get('findings', [])}
    java = r.get('java')
    tech = [f"Java {', '.join(java) if isinstance(java, list) else java}"] if java else []
    camel = r.get('camel')
    if camel:
        tech.append(f"Camel {', '.join(camel) if isinstance(camel, list) else camel}")
    tech += sorted((r.get('summary') or {}).get('integrationsByType', {}))
    tech += [t for t, rule in (('JBoss', 'JBOSS_DEPLOYMENT'), ('Spring XML', 'SPRING_XML'), ('javax.jms', 'JAKARTA_JMS'), ('JNDI', 'JNDI'),
                               ('Blueprint/OSGi', 'BLUEPRINT'), ('CDI', 'CDI_SCOPE')) if rule in rules]
    return tech


def analyze_all(ws: WorkspaceContext, analyze_fn, *, workers: int | None = None, log=print) -> WorkspaceContext:
    """Every project is analysed, by analysis waves (doc 20); failures stay per project; shared findings aggregated."""
    from . import analysis_wave_orchestrator
    analysis_wave_orchestrator.run(ws, analyze_fn, workers=workers, log=log)
    reports = {p.name: p.report for p in ws.projects if p.report is not None}
    ws.shared_findings = workspace.shared_findings(reports) if reports else None
    if ws.shared_findings:  # doc 18: [{rule, affectedProjects, totalProjects}] next to the COMMON_RULE list
        ws.shared_findings['workspaceRules'] = [
            {'rule': name.upper().replace(' ', '_').replace('.', '_') + '_WORKSPACE', 'affectedProjects': int(v.split('/')[0]),
             'totalProjects': int(v.split('/')[1])} for name, v in ws.shared_findings['metrics'].items() if not v.startswith('0/')]
    return ws


def counts(ws: WorkspaceContext) -> dict:
    by = lambda st: sum(p.analysis_status == st for p in ws.projects)
    return {'projectsDiscovered': len(ws.projects), 'projectsAnalyzed': sum(p.analysis_status in ('PASS', 'WARN', 'PARTIAL', 'FAIL') for p in ws.projects),
            'projectsPassed': by('PASS'), 'projectsWarn': by('WARN'), 'projectsPartial': by('PARTIAL'), 'projectsFailed': by('FAIL'),
            'internalDependencies': len(ws.dependency_graph['edges']), 'dependencyCycles': len(ws.order['cycles']),
            'migrationWaves': len(ws.order['waves'])}


ICON = {'PASS': '✓', 'WARN': '⚠', 'PARTIAL': '⚠', 'FAIL': '✗', 'BLOCKED': '⊘', 'SUCCEEDED': '✓', 'PARTIAL_SUCCESS': '⚠', 'FAILED': '✗'}


def project_report(p: ProjectContext) -> str:
    r = p.report or {}
    by = {}
    for f in p.findings:
        by[f['ruleId']] = by.get(f['ruleId'], 0) + 1
    lines = [f'# {p.name}', '', f"Estado del análisis: **{OUTCOME.get(p.analysis_status, p.analysis_status)}**" + (f' — {p.error}' if p.error else ''), '',
             f'- POM: {p.group_id}:{p.artifact_id}:{p.version} ({p.packaging})', f"- Tecnologías: {', '.join(p.technologies) or '—'}",
             f"- Integraciones: {len(p.integrations)} {(r.get('summary') or {}).get('integrationsByType', {})}",
             f"- Dependencias internas: {', '.join(p.dependencies) or 'ninguna'}", '', '## Hallazgos', '']
    lines += [f'- {k}: {v}' for k, v in sorted(by.items())] or ['- Ninguno']
    if p.dependency_context:
        lines += ['', '## Contexto heredado de dependencias internas', '']
        lines += [f"- {c['project']} ({c['analysis']}): {', '.join(c['findings']) or 'sin hallazgos'}" for c in p.dependency_context]
    if p.context_missing:
        lines += ['', f"Contexto incompleto (análisis parcial): falta el análisis de {', '.join(p.context_missing)}."]
    return '\n'.join(lines) + '\n'


def _head(ws: WorkspaceContext) -> list[str]:
    c = counts(ws)
    return ['```text', 'Workspace:', ws.info['workspaceRoot'], '', 'Repository type:', ws.info['repositoryType'], '',
            'Projects discovered:', str(c['projectsDiscovered']), '', 'Projects analyzed:', str(c['projectsAnalyzed']), '',
            'Projects passed:', str(c['projectsPassed'] + c['projectsWarn']), '', 'Projects partial:', str(c['projectsPartial']), '',
            'Projects failed:', str(c['projectsFailed']), '', 'Aggregator POM:', 'FOUND' if ws.info.get('aggregator') else 'NOT PRESENT', '',
            'Workspace Maven build:', 'NOT APPLICABLE (WORKSPACE_BUILD_NOT_APPLICABLE)' if ws.info['repositoryType'] == 'MULTI_PROJECT_REPOSITORY' else 'APPLICABLE', '',
            'Internal dependencies:', str(c['internalDependencies']), '', 'Dependency cycles:', str(c['dependencyCycles']), '',
            'Migration waves:', str(c['migrationWaves']), '', 'Analysis status:', ws.analysis_status]


def analysis_report(ws: WorkspaceContext) -> str:
    """workspace-analysis.md (doc 20 §8)."""
    by = {p.name: p for p in ws.projects}
    lines = ['# Análisis del workspace', ''] + _head(ws) + ['```', '', '## Análisis por oleadas', '']
    for w in ws.analysis_waves:
        lines += [f"### {w['wave']} — {w['status']}" + (f" ({w['note']})" if w.get('note') else '')]
        lines += [f"- {ICON.get(by[n].analysis_status, '·')} {n}: {OUTCOME.get(by[n].analysis_status, by[n].analysis_status)}"
                  + (f" (contexto incompleto: {', '.join(by[n].context_missing)})" if by[n].context_missing else '')
                  + (f' — {by[n].error}' if by[n].error else '') for n in w['projects']] + ['']
    lines += ['## Proyectos', '', '| Proyecto | Análisis | Tecnologías | Depende de |', '|---|---|---|---|']
    lines += [f"| {p.name} | {OUTCOME.get(p.analysis_status, p.analysis_status)} | {', '.join(p.technologies[:6])} | {', '.join(p.dependencies) or '—'} |"
              for p in ws.projects]
    if ws.order['blocked']:
        lines += ['', '## Fuera del orden automático de migración', ''] + [f'- {p}: {why}' for p, why in ws.order['blocked'].items()]
    if ws.shared_findings:
        lines += ['', '## Findings agregados', ''] + [f'- {k}: {v}' for k, v in ws.shared_findings['metrics'].items()]
        for cr in ws.shared_findings['commonRules']:
            lines += ['', f"### {cr['id']}", '', f"Detected: {cr['detected']}", f"Target: {cr['target']}", 'Affected:'] + [f'- {x}' for x in cr['projects']]
    lines += ['', 'No se crea ningún POM padre: cada proyecto se analiza y migra por separado; la migración respeta el grafo interno.']
    return '\n'.join(lines) + '\n'


def migration_report(ws: WorkspaceContext, results: dict) -> str:
    """workspace-migration.md (doc 20 §12, §17)."""
    built = sum(len(w['runnable']) for w in ws.migration_waves)
    lines = ['# Migración del workspace', '', '```text', 'Migration status:', ws.migration_status, '',
             'Workspace Maven build:', 'SKIPPED (WORKSPACE_BUILD_NOT_APPLICABLE)' if ws.info['repositoryType'] == 'MULTI_PROJECT_REPOSITORY' else 'APPLICABLE', '',
             'Projects discovered:', str(len(ws.projects)), '', 'Projects built:', str(built), '',
             'Projects reused:', str(sum(len(w['reused']) for w in ws.migration_waves)), '```', '',
             'Cada proyecto se construye con su propio POM (`mvn -f projects/<proyecto>/…/pom.xml`); nunca sobre la raíz del workspace.', '',
             '## Migración por oleadas', '']
    for w in ws.migration_waves:
        lines += [f"### {w['wave']} — {w['status']}"]
        for n in w['projects']:
            r = results.get(n, {})
            extra = f" — bloqueado por {', '.join(r['blockedBy'])}" if r.get('blockedBy') else (f" — {r['reason']}" if r.get('reason') else '')
            lines.append(f"- {ICON.get(r.get('status'), '⊘' if r.get('status') == 'BLOCKED' else '·')} {n}: {r.get('status', 'PENDING')}{extra}"
                         + (' (reutilizado del intento anterior)' if r.get('reused') else ''))
        lines.append('')
    outside = [n for n in results if not any(n in w['projects'] for w in ws.migration_waves)]
    if outside:
        lines += ['### Fuera de las oleadas', ''] + [f"- ⊘ {n}: BLOCKED — {results[n]['reason']}" for n in outside] + ['']
    lines += ['Un proyecto nunca intentado figura BLOCKED, no FAILED. Reintento individual: `migrate.py migrate <origen> --workspace '
              '--output <salida> --retry <proyecto>` (no repite los proyectos exitosos).']
    return '\n'.join(lines) + '\n'


def write_outputs(ws: WorkspaceContext, out: Path, results: dict | None = None):
    """Doc 20 §16: projects/<p>/analysis/{analysis.json, report.md} and workspace/ with the aggregated files."""
    from . import migration_wave_orchestrator as mig
    for p in ws.projects:
        d = out / 'projects' / p.name / 'analysis'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'report.md').write_text(project_report(p))
        if p.report is not None:
            (d / 'analysis.json').write_text(json.dumps(p.report, ensure_ascii=False, indent=2, default=str))
    wdir = out / 'workspace'
    wdir.mkdir(parents=True, exist_ok=True)
    w = lambda name, data: (wdir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    status = mig.workspace_status(ws.analysis_status, ws.migration_status if results is not None else None)
    from ..build.build_target_resolver import workspace_build_status
    w('workspace.json', {'repositoryType': ws.info['repositoryType'], 'workspaceRoot': ws.info['workspaceRoot'], 'aggregator': ws.info.get('aggregator'),
                         'workspaceBuild': workspace_build_status(ws.info),
                         'projectsBuilt': sum(len(x['runnable']) for x in ws.migration_waves) if results is not None else 0,
                         'workspaceStatus': status, 'analysisStatus': ws.analysis_status,
                         'migrationStatus': ws.migration_status if results is not None else None, 'counts': counts(ws),
                         'projects': [p.summary() | {'dir': str(p.root.relative_to(ws.workspace_root))} for p in ws.projects],
                         'findings': ws.info['findings']})
    w('dependency-graph.json', ws.dependency_graph)
    w('migration-order.json', {'order': ws.order['order'], 'blocked': ws.order['blocked'], 'cycles': ws.order['cycles']})
    w('analysis-waves.json', ws.analysis_waves)
    if ws.shared_findings is not None:
        w('shared-findings.json', ws.shared_findings)
    (wdir / 'workspace-analysis.md').write_text(analysis_report(ws))
    if results is not None:
        w('migration-waves.json', ws.migration_waves)
        w('workspace-state.json', {'workspaceStatus': status, 'projects': mig.state(ws, results)})
        (wdir / 'workspace-migration.md').write_text(migration_report(ws, results))
    return status

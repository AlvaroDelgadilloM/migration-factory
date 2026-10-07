"""Job handlers executed by the worker process (never by the API).

Delivery is at-least-once: a job is claimed through a DB lease, extended by a heartbeat. A retry
starts from a fresh workspace and replaces any partial results of the previous attempt.
"""
import html
import json
import logging
import os
import shutil
import socket
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, select, update

from .. import gates
from ..analysis.report import render as render_scan
from ..analysis.scanner import apply_effective, catalog_from_rules, scan as static_scan
from ..config import get_settings
from ..db import session
from ..models import (Artifact, DiffFile, Endpoint, Execution, Finding, Job, JobEvent, Module, Plan, Project, Repository, Route,
                      RuleCatalog, Scan, TargetProfile, Validation, now)
from ..planning import evaluate, runnable_recipe_steps
from ..security import UnsafeInput, find_secrets, redact, resolve_credential, safe_join, secret_files
from ..storage import ArtifactStore
from .. import preflight, transform
from . import gitops, mavenops, runner, source
from .runner import Cancelled, Context, TimedOut

log = logging.getLogger('mf.worker')
WORKER_ID = f'{socket.gethostname()}:{os.getpid()}'
CREDENTIALS = {}  # captured at worker start, then removed from os.environ (see main.py)


class StepFailure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# --- job lifecycle ------------------------------------------------------------------------------
def emit(job_id, level, phase, message):
    # improvements/08: one structured line per event (same text as the stored event, already redacted by callers)
    log.info(json.dumps({'jobId': str(job_id), 'phase': phase, 'severity': level, 'message': redact(message[:500])}, ensure_ascii=False))
    with session() as s:
        s.add(JobEvent(job_id=uuid.UUID(str(job_id)), level=level, phase=phase, message=message[:4000]))
        s.commit()


def claim(job_id: str):
    lease = get_settings().lease_seconds
    with session() as s:
        res = s.execute(update(Job).where(
            Job.id == uuid.UUID(job_id), Job.cancel_requested.is_(False), Job.attempts < Job.max_attempts,
            (Job.state == 'queued') | ((Job.state == 'running') & (Job.lease_until < now())))
            .values(state='running', lease_owner=WORKER_ID, lease_until=now() + timedelta(seconds=lease),
                    attempts=Job.attempts + 1, started_at=now(), error_code=None, error_message=None))
        s.commit()
        return s.get(Job, uuid.UUID(job_id)) if res.rowcount else None


class Heartbeat(threading.Thread):
    def __init__(self, ctx: Context):
        super().__init__(daemon=True)
        self.ctx, self.stopped, self.lost = ctx, threading.Event(), False

    def run(self):
        lease = get_settings().lease_seconds
        while not self.stopped.wait(max(2, lease / 3)):
            try:
                with session() as s:
                    res = s.execute(update(Job).where(Job.id == uuid.UUID(self.ctx.job_id), Job.lease_owner == WORKER_ID,
                                                      Job.state == 'running')
                                    .values(lease_until=now() + timedelta(seconds=lease)).returning(Job.cancel_requested))
                    row = res.first()
                    s.commit()
                if row is None:
                    self.lost = True
                    self.ctx.cancel.set()
                elif row[0]:
                    self.ctx.cancel.set()
            except Exception:  # transient DB error: keep trying until the lease would expire
                log.exception('heartbeat error')


def set_phase(ctx, phase, progress):
    ctx.phase = phase
    with session() as s:
        s.execute(update(Job).where(Job.id == uuid.UUID(ctx.job_id)).values(phase=phase, progress=progress))
        s.commit()
    ctx.emit('info', phase, f'Fase: {phase}')


def finish(job_id, state, code=None, message=None):
    with session() as s:
        job = s.get(Job, uuid.UUID(job_id))
        if job.lease_owner != WORKER_ID:
            return
        job.state, job.finished_at, job.lease_until = state, now(), None
        job.error_code, job.error_message = code, message and message[:4000]
        if state == 'succeeded':
            job.progress = 100
        if job.kind == 'scan':
            scan = s.get(Scan, job.subject_id)
            scan.status, scan.finished_at = state, now()
        elif job.kind == 'modernize':
            from ..models import Modernization
            s.get(Modernization, job.subject_id).state = state
        elif job.kind == 'workspace_migrate':
            from ..models import WorkspaceMigration
            s.get(WorkspaceMigration, job.subject_id).state = state
        elif job.kind in ('preview', 'apply'):
            ex = s.get(Execution, job.subject_id)
            ex.state = state
            ex.steps = [st | ({'state': 'cancelled'} if st['state'] in ('pending', 'running') and state in ('cancelled', 'timed_out', 'failed') else {})
                        for st in ex.steps]
        s.add(JobEvent(job_id=job.id, level='info' if state == 'succeeded' else ('warn' if state == 'partial_success' else 'error'), phase=state,
                       message=f'Trabajo {state}' + (f': {code} {message or ""}' if code else '')))
        s.commit()


def run_job(job_id: str):
    """RQ entry point."""
    job = claim(job_id)
    if job is None:
        log.info('job %s not claimable (done, cancelled or owned by another worker)', job_id)
        return
    s = get_settings()
    workdir = s.work_dir / job_id / f'attempt-{job.attempts}'
    workdir.mkdir(parents=True, exist_ok=True)
    limit = s.workspace_timeout_seconds if job.kind == 'workspace_migrate' else s.job_timeout_seconds
    ctx = Context(job_id, workdir, deadline=time.monotonic() + limit, emit=lambda l, p, m: emit(job_id, l, p, m))
    hb = Heartbeat(ctx)
    hb.start()
    if job.attempts > 1:
        ctx.emit('warn', 'retry', f'Reintento {job.attempts}: se descarta el workspace anterior y se repite desde cero')
    try:
        outcome = {'scan': do_scan, 'preview': do_execution, 'apply': do_execution, 'pull_request': do_pull_request,
                   'modernize': do_modernize, 'workspace_migrate': do_workspace_migrate}[job.kind](job, ctx)
        state, code, msg = outcome if outcome else ('succeeded', None, None)
        finish(job_id, state, code, msg)  # never a fixed 'succeeded': derived from the phases (doc 16 §8)
    except Cancelled:
        if not hb.lost:
            finish(job_id, 'cancelled', 'CANCELLED', 'Cancelado; el grupo de procesos fue detenido')
    except TimedOut:
        finish(job_id, 'timed_out', 'TIMEOUT', f'Superó el límite de {limit}s; procesos detenidos')
    except StepFailure as e:
        finish(job_id, 'failed', e.code, redact(str(e), ctx.secrets))
    except (gitops.GitError, UnsafeInput) as e:
        finish(job_id, 'failed', 'GIT_ERROR', redact(str(e), ctx.secrets))
    except Exception as e:
        log.exception('job %s failed', job_id)
        finish(job_id, 'failed', 'INTERNAL', redact(f'{type(e).__name__}: {e}', ctx.secrets)[:2000])
    finally:
        mavenops.use_local_overlay(None)  # thread-local Maven overlay (degraded mode / workspaces) never leaks into the next job
        hb.stopped.set()
        gitops.os_safe_rmtree(s.work_dir / job_id)  # checkout removed after artifacts are stored


def _token(repo: Repository, ctx):
    try:
        token = resolve_credential(repo.credential_ref, CREDENTIALS or None)
    except UnsafeInput as e:
        raise StepFailure('CREDENTIAL_UNAVAILABLE', str(e)) from None
    if repo.credential_ref and not token:
        raise StepFailure('CREDENTIAL_UNAVAILABLE', f'Referencia {repo.credential_ref} sin valor en el worker')
    return token


def _store(db, project_id, kind, name, data, content_type, ctx=None, commit=True, **kw):
    """Write + verify the blob, then register it (committed unless part of a larger transaction)."""
    if isinstance(data, str):
        data = (redact(data, ctx.secrets) if ctx else data).encode('utf-8')
    art = ArtifactStore().put(db, project_id=project_id, kind=kind, name=name, data=data, content_type=content_type, **kw)
    if commit:
        db.commit()
    return art


def _write_log(ctx, name, text):
    p = ctx.workdir / 'logs' / f'{name}.log'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _worktree_diff(ctx, repo):
    """Uncommitted changes (located edits) as a patch, including new files."""
    gitops._git(ctx, ['add', '-A'], repo, 'git-add-preview')
    return gitops._git(ctx, ['diff', '--cached', '--no-color', '--no-ext-diff'], repo, 'git-diff-preview')


def _materialize(db, repo, ref, dest, ctx, select=None):
    """Isolated working copy of the project source; the original is only read."""
    from ..models import Upload
    upload_path = None
    if repo.source_type == 'zip':
        up = db.get(Upload, repo.upload_id)
        if up is None:
            raise StepFailure('UPLOAD_MISSING', 'El ZIP del proyecto no existe')
        upload_path = ctx.workdir / 'upload.zip'
        data = (get_settings().artifact_dir / up.object_key).read_bytes()
        import hashlib as _h
        if _h.sha256(data).hexdigest() != up.sha256:
            raise StepFailure('UPLOAD_CORRUPT', 'El ZIP almacenado no coincide con su hash')
        upload_path.write_bytes(data)
    token = _token(repo, ctx) if repo.source_type == 'git' else None
    try:
        return source.materialize(ctx, repo, ref, dest, token, upload_path, select)
    except UnsafeInput as e:
        raise StepFailure(e.code, str(e)) from None


# --- scan ---------------------------------------------------------------------------------------
def do_scan(job: Job, ctx: Context):
    with session() as db:
        scan = db.get(Scan, job.subject_id)
        project = db.get(Project, scan.project_id)
        repo = db.get(Repository, project.repository_id)
        catalog_row = db.get(RuleCatalog, scan.catalog_id)
        scan.status, scan.started_at = 'running', now()
        db.commit()
        catalog = catalog_from_rules(catalog_row.version, catalog_row.rules)
        set_phase(ctx, 'snapshot', 10)
        src, root_rel, sha, repo_info = _materialize(db, repo, scan.ref, ctx.workdir / 'source', ctx, select=scan.project_root)
        selected = bool(scan.project_root)
        snap_file = ctx.workdir / 'snapshot.tar.gz'
        snapshot_hash = source.make_snapshot(src, snap_file)  # taken before any tool runs on the copy
        scan.commit_sha, scan.snapshot_hash, scan.project_root = sha, snapshot_hash, root_rel
        db.commit()
        ctx.emit('info', 'snapshot', f'Origen {repo.source_type}: snapshot {snapshot_hash[:16]}…'
                 + (f' · commit {sha}' if sha else '') + f' · raíz Maven {root_rel}')

        set_phase(ctx, 'static-analysis', 35)
        excluded = source.sensitive_files(src)
        report = static_scan(src, scan.target, catalog)
        report['errors'] += [{'file': f, 'code': 'SENSITIVE_FILE_EXCLUDED'} for f in excluded]
        report['workspace'] = _workspace_summary(repo_info, src, root_rel, selected, scan.target, catalog, ctx)
        ws_files = report['workspace'].pop('_files', {})  # stored as artifacts, never inside report.json
        ctx.emit('info', 'static-analysis', json.dumps(report['summary'], ensure_ascii=False))

        build_model = preflight.validate_model(src, report['modules'], report['errors'])
        repairs = preflight.repair(src, report['modules'], build_model)  # on the scan copy only; snapshot is the original
        for r in repairs:
            ctx.emit('info', 'preflight', f"Reparación de baseline: {r['file']}: {r['change']}")
        set_phase(ctx, 'maven-effective', 55)
        effective_art = None
        from ..build.build_target_resolver import NOT_APPLICABLE
        try:
            if report['workspace'].get('workspaceBuild', {}).get('status') == 'SKIPPED':
                # doc 21: a workspace without aggregator has no root build; Maven ran per ProjectContext in the workspace analysis
                ok = sum(p.get('mavenResolution') == 'maven-effective' for p in report['workspace']['projects'])
                scan.maven_resolution = 'per-project'
                scan.maven_resolution_detail = f"{NOT_APPLICABLE}: POM efectivo resuelto por proyecto ({ok}/{len(report['workspace']['projects'])})"
                ctx.emit('info', 'workspace-build', f"[workspace-build] repositoryType={report['workspace']['repositoryType']} status=SKIPPED reason={NOT_APPLICABLE}")
                raise _SkipRootBuild()
            res, out = mavenops.effective_pom(ctx, src)
            if res.exit_code == 0 and out.exists():
                report = apply_effective(report, mavenops.parse_effective(out), catalog)
                scan.maven_resolution, scan.maven_resolution_detail = 'maven-effective', 'mvn help:effective-pom en sandbox'
                effective_art = out.read_text(encoding='utf-8', errors='replace')
            else:
                scan.maven_resolution = 'failed'
                scan.maven_resolution_detail = res.log_text(ctx)[-1500:]
                ctx.emit('warn', 'maven-effective', 'Maven no pudo resolver el POM efectivo; se conservan valores estáticos marcados como no resueltos')
        except _SkipRootBuild:
            pass
        except (Cancelled, TimedOut):
            raise
        except Exception as e:  # Maven missing/misconfigured: keep static results, never pretend resolution
            scan.maven_resolution, scan.maven_resolution_detail = 'failed', redact(str(e), ctx.secrets)[:1500]
        ctx.check()

        set_phase(ctx, 'persist', 85)
        for model in (Finding, Module):
            db.execute(delete(model).where(model.scan_id == scan.id))
        for r in db.scalars(select(Route).where(Route.scan_id == scan.id)):
            db.delete(r)
        for a in db.scalars(select(Artifact).where(Artifact.scan_id == scan.id)):
            ArtifactStore().delete(a)
            db.delete(a)
        db.flush()
        for m in report['modules']:
            db.add(Module(scan_id=scan.id, path=m['file'], group_id=m['groupId'], artifact_id=m['artifactId'], version=m['version'],
                          packaging=m['packaging'], java_observed=m['javaObserved'], java_resolved=m['javaResolved'],
                          java_source=m['javaSource'], camel_versions=m['camelVersions'], resolution=m['resolution'],
                          unresolved=m['unresolved'] + [f'(estático) {u}' for u in m.get('staticUnresolved', [])],
                          dependencies=m['dependencies'], profiles=m['profiles']))
        for r in report['routes']:
            route = Route(scan_id=scan.id, route_key=r['key'], route_id=r['routeId'], dsl=r['dsl'], file=r['file'], line=r['line'],
                          detection=r['detection'], from_uri=r['fromUri'], dynamic_from=r['dynamicFrom'])
            db.add(route)
            db.flush()
            for e in r['endpoints']:
                db.add(Endpoint(route_id=route.id, component=e['component'], redacted_uri=e['uri'], dynamic=e['dynamic'], kind=e['kind'], line=e['line']))
        for f in report['findings']:
            db.add(Finding(scan_id=scan.id, project_id=scan.project_id, fingerprint=f['fingerprint'], rule_id=f['ruleId'],
                           rule_version=f['ruleVersion'], file=f['file'], line=f['line'], severity=f['severity'],
                           classification=f['classification'], blocking=f['blocking'], recommendation=f['recommendation'],
                           evidence=redact(f['evidence'])))
        report['commitSha'], report['snapshotHash'], report['sourceType'], report['mavenResolution'] = sha, snapshot_hash, repo.source_type, scan.maven_resolution
        snap = _store(db, scan.project_id, 'snapshot', 'snapshot.tar.gz', snap_file.read_bytes(), 'application/gzip', commit=False, scan_id=scan.id)
        scan.snapshot_artifact_id = snap.id
        _store(db, scan.project_id, 'report-json', 'report.json', json.dumps(report, ensure_ascii=False, indent=2), 'application/json', ctx, scan_id=scan.id, commit=False)
        _store(db, scan.project_id, 'report-html', 'report.html', render_scan(report), 'text/html; charset=utf-8', ctx, scan_id=scan.id, commit=False)
        if effective_art:
            _store(db, scan.project_id, 'effective-pom', 'effective-pom.xml', effective_art, 'application/xml', ctx, scan_id=scan.id, commit=False)
        for name, text in ws_files.items():  # doc 18: one report per project of the workspace
            _store(db, scan.project_id, 'report-json' if name.endswith('.json') else 'report-md', name, text,
                   'application/json' if name.endswith('.json') else 'text/markdown; charset=utf-8', ctx, scan_id=scan.id, commit=False)
        report['summary']['buildModel'] = build_model
        report['summary']['workspace'] = report['workspace']
        report['summary']['baselineRepairs'] = [{k: r[k] for k in ('file', 'kind', 'change', 'basis')} for r in repairs]
        scan.summary, scan.errors, scan.components = report['summary'], report['errors'], report['components']
        scan.route_builders = report.get('routeBuilders', [])
        db.commit()


class _SkipRootBuild(Exception):
    """The workspace root is not a Maven project (doc 21): nothing to resolve there."""


def _project_analysis(p, target, catalog, ctx):
    """Static analysis + effective POM of ONE ProjectContext, in its own work directory (never the workspace root)."""
    from ..build.build_target_resolver import for_project
    r = static_scan(p.root, target, catalog)
    if not for_project(p.root).buildable:
        r['mavenResolution'] = 'not-a-project'
        return r
    pctx = Context(ctx.job_id, ctx.workdir / 'projects' / p.name.replace('/', '__'), deadline=ctx.deadline, cancel=ctx.cancel, secrets=ctx.secrets)
    pctx.workdir.mkdir(parents=True, exist_ok=True)
    try:
        res, out = mavenops.effective_pom(pctx, p.root)
        if res.exit_code == 0 and out.exists():
            r = apply_effective(r, mavenops.parse_effective(out), catalog)
            r['mavenResolution'] = 'maven-effective'
        else:
            r['mavenResolution'] = 'failed'
    except (Cancelled, TimedOut):
        raise
    except Exception:  # Maven unavailable for this project: static values stay marked as unresolved
        r['mavenResolution'] = 'failed'
    return r


def _workspace_summary(info, src, root_rel, selected, target, catalog, ctx):
    """Doc 17/18: repository type and, for a workspace, every project analysed by the MultiProjectOrchestrator (parallel,
    failures isolated per project), internal graph, order/waves, cycles, shared findings and one report per project."""
    from .. import workspace
    from ..orchestration import multi_project_orchestrator as orch
    out = {'repositoryType': info['repositoryType'], 'workspaceRoot': info['workspaceRoot'], 'selectedProject': root_rel if selected else None,
           'projects': [{'name': p['name'], 'dir': p['dir']} for p in info['projects']], 'findings': info['findings']}
    if info['repositoryType'] != workspace.MULTI_PROJECT or selected:
        return out
    from ..build.build_target_resolver import workspace_build_status
    ws = orch.build_context(src)  # src is the workspace root when nothing was selected
    workers = int(os.environ.get('MF_WORKSPACE_MAVEN_PARALLELISM', '2'))  # one JVM per project: bounded by the container memory
    orch.analyze_all(ws, lambda p: _project_analysis(p, target, catalog, ctx), workers=workers, log=lambda m: ctx.emit('info', 'workspace', m))
    status = {p.name: p for p in ws.projects}
    out['projects'] = [x | {k: v for k, v in status[x['name']].summary().items() if k in ('analysisStatus', 'analysis', 'contextMissing', 'technologies',
                                                                                          'findings', 'integrations', 'error', 'dependencies')}
                       for x in out['projects']]
    for x in out['projects']:
        x['mavenResolution'] = (status[x['name']].report or {}).get('mavenResolution')
    out |= {'workspaceBuild': workspace_build_status(info), 'graph': ws.dependency_graph, 'order': ws.order, 'shared': ws.shared_findings,
            'analysisStatus': ws.analysis_status,
            'analysisWaves': ws.analysis_waves,
            'counts': orch.counts(ws),
            '_files': {f'{p.name}.report.md': orch.project_report(p) for p in ws.projects}
            | {f'{p.name}.analysis.json': json.dumps(p.report, ensure_ascii=False, indent=2, default=str) for p in ws.projects if p.report}
            | {'workspace-analysis.md': orch.analysis_report(ws)}}
    c = out['counts']
    ctx.emit('info', 'workspace', f"{workspace.MULTI_PROJECT}: {c['projectsDiscovered']} proyectos, {c['projectsAnalyzed']} analizados, "
             f"{c['projectsFailed']} con fallo, {c['internalDependencies']} dependencias internas, {c['migrationWaves']} oleadas, "
             f"{c['dependencyCycles']} ciclos")
    return out


# --- preview / apply ----------------------------------------------------------------------------
def _save_steps(db, ex, steps):
    ex.steps = [dict(s) for s in steps]  # reassign: JSON column change tracking
    db.commit()


def _validation(db, ex, ctx, suite, target, gate, res: runner.Result | None, status=None, tests=None, detail=None):
    art = None
    if res is not None:
        art = _store(db, ex.project_id, 'log', f'{target}-{suite}.log', res.log_text(ctx), 'text/plain; charset=utf-8', ctx, execution_id=ex.id)
        status = status or ('PASS' if res.exit_code == 0 else 'FAIL')
    v = Validation(execution_id=ex.id, suite=suite, target=target, gate=gate, status=status, tests=tests, detail=detail,
                   exit_code=res.exit_code if res else None, duration_ms=res.duration_ms if res else None, artifact_id=art.id if art else None)
    db.add(v)
    db.commit()
    ctx.emit('info' if status == 'PASS' else 'warn', ctx.phase, f'{target}/{suite}: {status}' + (f' {tests}' if tests else ''))
    return v


def _build_validations(db, ex, ctx, repo_dir, target, skip_tests_reason=None):
    """Returns the structured javac errors of the compile (doc 28) and the classified log errors."""
    from .. import compile_errors
    ctx.phase = f'{target}-compile'
    comp = mavenops.mvn(ctx, repo_dir, ['test-compile'], f'{target}-compile')
    _validation(db, ex, ctx, 'compile', target, 'G2' if target == 'candidate' else 'G0', comp)
    text = comp.log_text(ctx)
    ctx.last_build = {'java': compile_errors.summary(text, comp.exit_code != 0), 'classified': preflight.classify(text), 'text': text[-200000:],
                      'compileOk': comp.exit_code == 0}
    if comp.exit_code != 0:
        _validation(db, ex, ctx, 'test', target, 'G3' if target == 'candidate' else 'G0', None, status='SKIPPED',
                    detail='SKIPPED_DUE_TO_COMPILE_FAILURE: no se ejecuta porque la compilación falló')
        return
    if skip_tests_reason:  # doc 32: runtime-scope artifacts missing: the compiler ran, the tests cannot
        _validation(db, ex, ctx, 'test', target, 'G3' if target == 'candidate' else 'G0', None, status='NOT_RUN', detail=skip_tests_reason)
        return
    ctx.phase = f'{target}-test'
    test = mavenops.mvn(ctx, repo_dir, ['test'], f'{target}-test')
    _validation(db, ex, ctx, 'test', target, 'G3' if target == 'candidate' else 'G0', test, tests=mavenops.surefire_summary(repo_dir))
    if test.exit_code != 0:
        ctx.last_build |= {'testText': test.log_text(ctx)[-200000:], 'testOk': False}


def do_execution(job: Job, ctx: Context):
    with session() as db:
        ex = db.get(Execution, job.subject_id)
        plan = db.get(Plan, ex.plan_id)
        project = db.get(Project, ex.project_id)
        repo = db.get(Repository, project.repository_id)
        profile = db.get(TargetProfile, plan.profile_id)
        # idempotent retry: drop partial results of earlier attempts
        for model in (DiffFile, Validation):
            db.execute(delete(model).where(model.execution_id == ex.id))
        for a in db.scalars(select(Artifact).where(Artifact.execution_id == ex.id)):
            ArtifactStore().delete(a)
            db.delete(a)
        ex.state, ex.candidate_sha = 'running', None
        db.commit()

        evaluated = evaluate(db, plan)  # re-evaluated at run time: a re-opened finding blocks again
        runnable = runnable_recipe_steps(evaluated)
        if not runnable:
            raise StepFailure('NOTHING_RUNNABLE', 'Ningún paso automatizable está listo')
        steps = [{'key': s['key'], 'title': s['title'], 'kind': s['kind'], 'state': 'pending' if s in runnable else 'skipped',
                  'reasons': s['blockedReasons'], 'recipes': (s['recipe'] or {}).get('activeRecipes', []), 'dependsOn': s['dependsOn']}
                 for s in evaluated if s['kind'] in ('recipe', 'transform')]
        _save_steps(db, ex, steps)
        tooling = profile.tooling

        set_phase(ctx, 'snapshot', 5)
        scan = db.get(Scan, plan.scan_id)
        snap_art = db.get(Artifact, scan.snapshot_artifact_id) if scan.snapshot_artifact_id else None
        if snap_art is None:
            raise StepFailure('SNAPSHOT_MISSING', 'El análisis no tiene snapshot; repita el análisis')
        snap_file = ctx.workdir / 'snapshot.tar.gz'
        snap_file.write_bytes(ArtifactStore().read(snap_art))  # sha256 verified by the store
        src = ctx.workdir / 'candidate'
        source.restore_snapshot(snap_file, src, ex.base_snapshot or scan.snapshot_hash)
        sha = gitops.init_snapshot_repo(ctx, src)  # internal history for per-step commits, diff and rollback
        mods = [{'file': m.path, 'dependencies': m.dependencies} for m in db.scalars(select(Module).where(Module.scan_id == scan.id))]
        repairs = preflight.repair(src, mods, [])
        repaired = gitops.commit_all(ctx, src, 'migration-factory: baseline-repair') if repairs else None
        for r in repairs:
            ctx.emit('info', 'preflight', f"Reparación de baseline: {r['file']}: {r['change']}")
        ctx.emit('info', 'snapshot', f'Copia de trabajo restaurada desde snapshot {(ex.base_snapshot or "")[:16]}…')
        if repaired:
            steps.insert(0, {'key': 'baseline-repair', 'title': 'Reparación de baseline (POM)', 'kind': 'baseline', 'state': 'applied',
                             'commit': repaired, 'parent': sha, 'files': gitops.changed_files(ctx, src, sha, repaired),
                             'recipes': [], 'reasons': [r['change'] for r in repairs], 'dependsOn': []})
            _save_steps(db, ex, steps)
        validate_model = lambda name: preflight.model_check(lambda p, g, n: mavenops.mvn(ctx, p, g, n), src, name)
        mcheck = validate_model('candidate-maven-model')
        ctx.emit('info', 'preflight', f"candidate/maven-model: {'PASS' if mcheck['status'] == 'VALID' else 'FAIL'}")
        _validation(db, ex, ctx, 'maven-model', 'candidate', 'G1', None, status='PASS' if mcheck['status'] == 'VALID' else 'FAIL',
                    detail='; '.join(e['message'] for e in mcheck['errors'][:3]) or 'mvn validate')
        if mcheck['status'] != 'VALID':  # recipes never run on an invalid Maven model (doc 15 §8, doc 16 §4)
            from ..baseline import vendor_bom_analyzer as vba  # doc 42: an unresolvable vendor platform BOM is named as such
            mirror, repos = vba.configured_repositories()
            plat = vba.evaluate((scan.summary or {}).get('platform'), mods, mcheck['errors'], repairs, repos, mirror)
            blocked = bool(plat and plat['blocked'])
            primary = plat['primaryReason'] if blocked else 'CANDIDATE_MAVEN_MODEL_INVALID'
            for st in steps:
                if st['state'] == 'pending':
                    st.update(state='blocked-by-baseline', reasons=['VENDOR_BOM_UNRESOLVED' if blocked else 'CANDIDATE_MAVEN_MODEL_INVALID'])
            _save_steps(db, ex, steps)
            ex.summary = {'status': 'BLOCKED', 'primaryReason': primary,
                          'reasonCodes': list(dict.fromkeys((plat['reasonCodes'] + ['BASELINE_BLOCKED_PLATFORM_BOM'] if blocked else [])
                                                            + ['CANDIDATE_MAVEN_MODEL_INVALID'])),
                          'detectedIssues': [e['message'] for e in mcheck['errors'][:10]]}
            if plat:
                ex.summary['baseline'] = 'BASELINE_BLOCKED_PLATFORM_BOM' if blocked else None
                ex.summary['platform'] = {k: plat.get(k) for k in ('platform', 'signals', 'vendorBoms', 'repair', 'continuation', 'primaryReason',
                                                                   'requiredAction', 'runtimeMigration')}
                _store(db, ex.project_id, 'report-json', 'dependency-management-snapshot.json', json.dumps(plat['snapshot'], indent=2),
                       'application/json', ctx, execution_id=ex.id, commit=False)
            db.commit()
            return ('blocked', primary, '; '.join(e['message'] for e in mcheck['errors'][:3]) or 'mvn validate falló')
        meta = ctx.workdir / 'meta'
        meta.mkdir(exist_ok=True)

        if ex.mode == 'preview':
            set_phase(ctx, 'dry-run', 30)
            cfg = meta / 'rewrite.yml'
            cfg.write_text(''.join((s['recipe'] or {}).get('yaml', '') for s in runnable) or '---\n')
            active = [r for s in runnable for r in (s['recipe'] or {}).get('activeRecipes', [])]
            patch = ''
            if active:
                res = mavenops.rewrite(ctx, src, 'dryRun', plugin_version=tooling['pluginVersion'], artifacts=tooling['artifacts'],
                                       active=active, config=cfg, name='rewrite-dryRun')
                _store(db, ex.project_id, 'log', 'rewrite-dryRun.log', res.log_text(ctx), 'text/plain; charset=utf-8', ctx, execution_id=ex.id)
                if res.exit_code != 0:
                    raise StepFailure('RECIPE_FAILED', 'dryRun falló; ver log de la ejecución')
                patch = mavenops.collect_patches(src)
            for s in runnable:  # located edits are previewed by applying them to the throwaway copy
                if (s['recipe'] or {}).get('edits'):
                    transform.apply_edits(src, s['recipe']['edits'], profile.runtime)
            patch = redact(patch + _worktree_diff(ctx, src), ctx.secrets)
            set_phase(ctx, 'diff', 80)
            for path, kind, add, dele, text, recipes in mavenops.split_patch(patch):
                db.add(DiffFile(execution_id=ex.id, path=path, change_type=kind, additions=add, deletions=dele, recipes=recipes, patch=text))
            if patch:
                _store(db, ex.project_id, 'patch', 'rewrite-dryRun.patch', patch, 'text/x-diff; charset=utf-8', ctx, execution_id=ex.id)
            for st in steps:
                if st['state'] == 'pending':
                    st['state'] = 'previewed'
            ex.summary = {'outcome': 'preview', 'filesChanged': len(mavenops.split_patch(patch)), 'sourceUnchanged': True}
            _save_steps(db, ex, steps)
            _report(db, ex, plan, profile, ctx)
            return

        # apply: baseline first (G0), then one commit per recipe step
        set_phase(ctx, 'baseline', 10)
        base_dir = ctx.workdir / 'baseline'
        gitops.worktree(ctx, src, repaired or sha, base_dir)  # baseline = legacy (+ low-risk repair), never migrated
        _build_validations(db, ex, ctx, base_dir, 'baseline')
        gitops.os_safe_rmtree(base_dir)
        # doc 32: environment (dependency that cannot be resolved) vs code; policy -> CONTINUE / DEGRADED / BLOCKED
        from ..baseline import dependency_resolution_classifier as drc
        from ..cli_tool import baseline_policy
        bb = getattr(ctx, 'last_build', None) or {}
        mods = list(db.scalars(select(Module).where(Module.scan_id == scan.id)))
        internal = {f'{m.group_id}:{m.artifact_id}' for m in mods}
        orgs = {'.'.join(m.group_id.split('.')[:2]) for m in mods if m.group_id and '.' in m.group_id}
        issues = drc.classify(bb.get('text', ''), internal=internal, org_prefixes=orgs) if not bb.get('compileOk', True) else []
        tests_only = False
        if not issues and bb.get('testOk') is False:  # runtime/test-scope artifacts are only resolved by the test phase
            issues = drc.classify(bb.get('testText', ''), internal=internal, org_prefixes=orgs)
            tests_only = bool(issues)
        bvals = {(v.suite, v.target): v for v in db.scalars(select(Validation).where(Validation.execution_id == ex.id))}
        b_ok = bb.get('compileOk', False) and getattr(bvals.get(('test', 'baseline')), 'status', 'PASS') in ('PASS', 'SKIPPED')
        baseline_state = preflight.state(b_ok, repairs, bb.get('classified', []), issues)
        decision = drc.decide(baseline_state, issues, baseline_policy())
        if issues and bvals.get(('test' if tests_only else 'compile', 'baseline')):  # §18: the build was NOT_EXECUTABLE, it did not FAIL
            v = bvals[('test' if tests_only else 'compile', 'baseline')]
            v.status = 'BLOCKED'
            v.detail = f"{drc.BUILD_STATE.get(baseline_state)}: " + '; '.join(f"{i['artifact']} ({i['reason']})" for i in issues)
            if bvals.get(('test', 'baseline')) and not tests_only:
                bvals[('test', 'baseline')].status, bvals[('test', 'baseline')].detail = 'NOT_RUN', 'BUILD_NOT_EXECUTABLE: DEPENDENCY_RESOLUTION_BLOCKED'
            db.commit()
            ctx.emit('warn', 'baseline', f"{baseline_state}: {v.detail} · migración {decision['migration']}")
        base_info = {'baseline': baseline_state, 'baselineDependencies': issues, 'baselineDecision': decision,
                     'migrationMode': 'DEGRADED' if decision['migration'] == 'DEGRADED' else 'NORMAL'}
        if decision['migration'] == 'BLOCKED':
            for st in steps:
                if st['state'] == 'pending':
                    st.update(state='blocked-by-baseline', reasons=[f"{decision['code']}: {decision['reason']}"])
            _save_steps(db, ex, steps)
            ex.summary = {'status': 'BLOCKED', 'primaryReason': decision['code'], 'reasonCodes': [decision['code'], baseline_state], **base_info,
                          'rootCause': {'primaryReason': baseline_state, 'rootCause': issues[0]['reason'], 'artifact': issues[0]['artifact'],
                                        'recommendedAction': issues[0].get('hint')}}
            db.commit()
            _report(db, ex, plan, profile, ctx)
            return ('blocked', decision['code'], decision['reason'])
        degraded = decision['migration'] == 'DEGRADED'
        if degraded:  # stubs of the missing low-criticality artifacts only so OpenRewrite can parse and run the recipes
            from ..baseline.stubs import install_stubs
            head = ctx.workdir / '.m2-degraded'
            mavenops.use_local_overlay(head)
            install_stubs(head, [i['artifact'] for i in issues])
            ctx.emit('warn', 'baseline', f"Modo DEGRADADO: {decision['reason']}")

        failed = set()
        total = len([s for s in steps if s['state'] == 'pending'])
        for i, st in enumerate([s for s in steps if s['state'] == 'pending']):
            set_phase(ctx, f'recipe:{st["key"]}', 25 + int(40 * i / max(total, 1)))
            if any(d in failed for d in st['dependsOn']):
                st.update(state='skipped', reasons=[f'Depende de un paso fallido: {sorted(failed & set(st["dependsOn"]))}'])
                failed.add(st['key'])
                _save_steps(db, ex, steps)
                continue
            st['state'] = 'running'
            _save_steps(db, ex, steps)
            step_def = next(s for s in runnable if s['key'] == st['key'])
            cfg = meta / f'rewrite-{st["key"]}.yml'
            cfg.write_text(step_def['recipe'].get('yaml') or '---\n')
            prev = gitops.head(ctx, src)
            if step_def['recipe'].get('pomMapping'):  # doc 24 phase 2: located POM edit, no artifact resolution needed
                from ..pom import normalizer
                results = normalizer.run_mapping_step(src, step_def['recipe']['pomMapping'])
                st['edits'] = results
                res = runner.Result(0, 0, _write_log(ctx, f'transform-{st["key"]}', json.dumps(results, ensure_ascii=False, indent=2)))
            elif step_def['recipe'].get('edits'):
                results = transform.apply_edits(src, step_def['recipe']['edits'], profile.runtime)
                st['edits'] = results
                res = runner.Result(0 if all(r['applied'] for r in results) else 1, 0,
                                    _write_log(ctx, f'transform-{st["key"]}', json.dumps(results, ensure_ascii=False, indent=2)))
            else:
                res = mavenops.rewrite(ctx, src, 'run', plugin_version=tooling['pluginVersion'], artifacts=tooling['artifacts'],
                                       active=step_def['recipe']['activeRecipes'], config=cfg, name=f'rewrite-{st["key"]}')
            art = _store(db, ex.project_id, 'log', f'rewrite-{st["key"]}.log', res.log_text(ctx), 'text/plain; charset=utf-8', ctx, execution_id=ex.id)
            st['logArtifactId'], st['durationMs'], st['exitCode'] = str(art.id), res.duration_ms, res.exit_code
            if res.exit_code != 0:
                gitops.reset_hard(ctx, src, prev)  # step-level rollback
                st.update(state='failed-rolled-back', reasons=['La receta falló; el candidato volvió al commit anterior'])
                failed.add(st['key'])
                ctx.emit('error', ctx.phase, f'Paso {st["key"]} falló y se revirtió')
            elif st['kind'] == 'recipe' and (after := validate_model(f'maven-model-after-{st["key"]}'))['status'] != 'VALID':
                gitops.reset_hard(ctx, src, prev)  # the recipe broke the Maven model: never keep that state
                st.update(state='failed-rolled-back', reasons=['CANDIDATE_MAVEN_MODEL_INVALID tras la receta: '
                                                               + '; '.join(e['message'] for e in after['errors'][:3])])
                failed.add(st['key'])
                ctx.emit('error', ctx.phase, f'Paso {st["key"]} dejó el POM inválido y se revirtió')
            else:
                from ..pom import normalizer
                norm = normalizer.normalize(src)  # doc 23: after every phase that may touch the POM
                if norm['changes'] or norm['conflicts']:
                    st['pomNormalization'] = norm
                commit = gitops.commit_all(ctx, src, f'migration-factory: {st["key"]}\n\nRecetas: {", ".join(st["recipes"])}')
                st.update(state='applied' if commit else 'no-changes', commit=commit, parent=prev,
                          files=gitops.changed_files(ctx, src, prev, commit) if commit else [])
            _save_steps(db, ex, steps)

        n_changes = _ledger(db, ex, ctx, src, steps, evaluated, scan)  # improvements/14: per-step change record
        tip = gitops.head(ctx, src)
        ex.candidate_sha = tip
        db.commit()
        set_phase(ctx, 'diff', 70)
        recipe_by_file = {}
        for st in steps:
            for f in st.get('files', []):
                recipe_by_file.setdefault(f, []).append(st['key'])
        patch = ''
        if tip != sha:
            for path, kind, add, dele, text in gitops.diff_files(ctx, src, sha, tip):
                db.add(DiffFile(execution_id=ex.id, path=path, change_type=kind, additions=add, deletions=dele,
                                recipes=recipe_by_file.get(path, []), patch=redact(text, ctx.secrets)))
            patch = gitops.full_patch(ctx, src, sha, tip)
            _store(db, ex.project_id, 'patch', 'candidate.patch', patch, 'text/x-diff; charset=utf-8', ctx, execution_id=ex.id)
        db.commit()  # candidate.zip is packaged at the end, with the final status in its manifest (doc 19 §15)

        set_phase(ctx, 'secret-scan', 75)
        added = '\n'.join(l[1:] for l in patch.splitlines() if l.startswith('+') and not l.startswith('+++'))
        try:
            hits = find_secrets(added)
            files = secret_files(src)  # also secrets that were already in the source and survived into the candidate
            _validation(db, ex, ctx, 'secret-scan', 'candidate', 'G5', None, status='FINDINGS' if hits or files else 'PASS',
                        detail=(f'{len(hits)} patrón(es) en líneas añadidas; ' if hits else '')
                        + (f"secretos en: {', '.join(files[:10])}" if files else 'Sin secretos en el candidato'))
        except Exception as e:  # a scanner failure is a tool error, never a finding
            _validation(db, ex, ctx, 'secret-scan', 'candidate', 'G5', None, status='ERROR', detail=f'SCANNER_EXECUTION_FAILED: {type(e).__name__}')

        set_phase(ctx, 'dependency-validation', 78)
        from .. import camel_compat
        decisions = next(((s['recipe'] or {}).get('compat', []) for s in evaluated if s['key'] == 'camel-3-to-4'), [])
        if decisions:
            _store(db, ex.project_id, 'report-md', 'camel-component-migration-report.md', camel_compat.report_markdown(decisions),
                   'text/markdown; charset=utf-8', ctx, execution_id=ex.id, commit=False)
        from ..dependency import preflight_gates
        dg = preflight_gates.run(src, profile.tooling['camelRecipeTarget'], usage=(scan.summary or {}).get('usage') or {}, decisions=decisions)
        suites = {'POM_SANITY_GATE': 'pom-sanity', 'CAMEL_COMPONENT_COMPATIBILITY_GATE': 'camel-compatibility',
                  'CAMEL_VERSION_CONSISTENCY_GATE': 'camel-version', 'ARTIFACT_AVAILABILITY_GATE': 'artifact-availability',
                  'DEPENDENCY_VALIDATION_GATE': 'dependency-validation'}
        for g in dg['gates']:
            detail = g.get('reason') or g.get('code') or ''
            if g.get('conflicts'):
                detail += ': ' + ', '.join(f"{c['artifact']}:{c['version']}" for c in g['conflicts'][:10])
            if g.get('findings'):
                detail += ': ' + ', '.join(f"{c['artifact']} → {c['recommendedAction']}" for c in g['findings'][:10])
            if g.get('issues'):
                detail += ': ' + ', '.join(f"{c['issue']} {c['detail']}" for c in g['issues'][:10])
            if g.get('reasons'):
                detail += ': ' + '; '.join(g['reasons'][:5])
            _validation(db, ex, ctx, suites[g['name']], 'candidate', 'G1', None, status=g['status'], detail=(detail or g['status'])[:4000])
            ctx.emit('info' if g['status'] == 'PASS' else 'warn', 'preflight-gates', f"{g['name']}: {g['status']} {g.get('code') or ''}")
        set_phase(ctx, 'candidate-build', 80)
        if dg['status'] == 'FAIL':  # docs 22/29: never wait minutes for Maven to fail on a candidate known to be invalid
            for suite, gate_id in (('compile', 'G2'), ('test', 'G3')):
                _validation(db, ex, ctx, suite, 'candidate', gate_id, None, status='SKIPPED',
                            detail=f"BLOCKED_BY_PREFLIGHT_GATE: {dg['primary']}")
        elif ex.mode == 'apply' and degraded and tests_only:
            mavenops.use_local_overlay(None)
            _build_validations(db, ex, ctx, src, 'candidate',
                               skip_tests_reason='BUILD_NOT_EXECUTABLE: DEPENDENCY_RESOLUTION_BLOCKED: ' + ', '.join(i['artifact'] for i in issues))
        elif ex.mode == 'apply' and degraded:  # doc 32: the build cannot be reproduced without the missing artifacts
            mavenops.use_local_overlay(None)
            why = 'BUILD_NOT_EXECUTABLE: DEPENDENCY_RESOLUTION_BLOCKED: ' + ', '.join(i['artifact'] for i in issues)
            for suite, gate_id in (('compile', 'G2'), ('test', 'G3')):
                _validation(db, ex, ctx, suite, 'candidate', gate_id, None, status='NOT_RUN', detail=why)
            ctx.last_build = None
        else:
            _build_validations(db, ex, ctx, src, 'candidate')
        build = getattr(ctx, 'last_build', None) or {'java': {'errors': [], 'parser': 'NOT_RUN', 'count': 0, 'byType': {}}, 'classified': []}
        vals = {(v.suite, v.target): v.status for v in db.scalars(select(Validation).where(Validation.execution_id == ex.id).order_by(Validation.created_at))}
        camel_step = next((s for s in evaluated if s['key'] == 'camel-3-to-4'), None)
        compat_codes = [d['code'] for d in decisions if d['action'] == 'BLOCK'] if camel_step and camel_step['state'] == 'blocked' else []
        agg = aggregate_status(steps, vals, plan_blocked=[s for s in evaluated if s['kind'] == 'manual' and s['state'] == 'blocked'],
                               manual_actions=sum(1 for s in evaluated if s['kind'] == 'manual' and s['state'] in ('blocked', 'pending')),
                               not_executable=ex.mode == 'apply' and degraded,
                               leading=(['CAMEL_2_TO_3_DECISION_REQUIRED'] if any(s['key'] == 'camel-2-to-3' and s['state'] == 'blocked' for s in evaluated) else [])
                               + ([dg['primary']] if dg['status'] == 'FAIL' else []) + compat_codes
                               + build_root_causes(vals.get(('compile', 'candidate')), vals.get(('test', 'candidate')), build['classified'],
                                                   build['java'].get('errors')))
        from ..cli_tool import root_cause
        agg['rootCause'] = root_cause({'primaryReason': agg['primaryReason'], 'reasonCodes': agg['reasonCodes'], 'status': agg['status'],
                                       'rootCause': dg['rootCause'], 'compileErrors': build['java']}, decisions, profile.tooling['camelRecipeTarget'])
        agg['compileErrors'] = {k: build['java'][k] for k in ('parser', 'count', 'byType')} | {'errors': build['java'].get('errors', [])[:50]}
        if ex.mode == 'apply' and degraded:
            agg['rootCause'] = {'primaryReason': baseline_state, 'rootCause': issues[0]['reason'], 'artifact': issues[0]['artifact'],
                                'recommendedAction': issues[0].get('hint'), 'secondary': agg['reasonCodes'], 'migration': 'DEGRADED'}
        ex.summary = {
            **agg,
            **(base_info if ex.mode == 'apply' else {}),
            'validationConfidence': drc.confidence_factors(b_ok, not issues, vals.get(('compile', 'candidate')) == 'PASS' if not degraded else None,
                                                           vals.get(('test', 'candidate')) == 'PASS' if not degraded else None)['level'] if ex.mode == 'apply' else None,
            'baseline': baseline_state if ex.mode == 'apply' else None,
            'baselineRepairs': [{k: r[k] for k in ('file', 'change', 'basis')} for r in repairs],
            'outcome': 'validated' if vals.get(('compile', 'candidate')) == 'PASS' and vals.get(('test', 'candidate')) == 'PASS' else 'validations-failed',
            'stepsApplied': sum(s['state'] == 'applied' for s in steps), 'stepsFailed': sum(s['state'] == 'failed-rolled-back' for s in steps),
            'filesChanged': db.query(DiffFile).filter(DiffFile.execution_id == ex.id).count(), 'sourceUnchanged': True,
            'changesRecorded': n_changes,
            'note': 'Compilar y pasar pruebas unitarias no demuestra equivalencia funcional (ver gate G4).'}
        db.commit()
        set_phase(ctx, 'package', 92)
        _package_result(db, ex, ctx, src, repo, profile, plan, evaluated, redact(patch, ctx.secrets))
        set_phase(ctx, 'report', 95)
        _report(db, ex, plan, profile, ctx)
        ctx.emit('info' if agg['status'] == 'SUCCEEDED' else 'warn', 'status',
                 f"Migration status: {agg['status']}" + (f" · motivo: {agg['primaryReason']}" if agg['primaryReason'] else ''))
        return (agg['status'].lower(), agg['primaryReason'], ', '.join(agg['reasonCodes']) or None)


def _ledger(db, ex, ctx, src, steps, evaluated, scan) -> int:
    """changes.json + one patch artifact per applied step (rule, lines, before/after, level, rollback)."""
    from .. import ledger
    rule_of = {str(f.id): f.rule_id for f in db.scalars(select(Finding).where(Finding.scan_id == scan.id))}
    plan_steps = [s | {'findings': [{'ruleId': rule_of[i]} for i in s['findingIds'] if i in rule_of]} for s in evaluated]
    plan_steps.append({'key': 'baseline-repair', 'kind': 'baseline', 'classification': 'AUTO',
                       'recipe': {'rules': ['MAVEN-CAMEL-MISSING-VERSION']}, 'findings': []})
    applied = [st for st in steps if st['state'] == 'applied' and st.get('commit') and st.get('parent')]
    patches = {st['key']: redact(gitops.full_patch(ctx, src, st['parent'], st['commit']), ctx.secrets) for st in applied}
    summary = scan.summary or {}
    factors = ledger.project_factors({'findings': [{'ruleId': r} for r in rule_of.values()], 'testClasses': summary.get('testClasses', 0),
                                      'summary': summary})
    entries = ledger.build([{'key': st['key'], 'state': 'APPLIED'} for st in applied], {'steps': plan_steps}, patches, factors,
                           {st['key']: (st['parent'], st['commit']) for st in applied})
    for e in entries:
        name = e['patch'].replace('steps/', 'step-')
        art = _store(db, ex.project_id, 'patch', name, patches[e['step']], 'text/x-diff; charset=utf-8', ctx, execution_id=ex.id, commit=False)
        e['patch'], e['patchArtifactId'] = name, str(art.id)
        e['rollback']['command'] = f'git apply -R {name}'
    _store(db, ex.project_id, 'changes-json', 'changes.json', json.dumps(entries, ensure_ascii=False, indent=2), 'application/json', ctx,
           execution_id=ex.id, commit=False)
    return len(entries)


# --- modernization (12-PROMPT-CLAUDE-MODERNIZATION) -----------------------------------------------
MOD_REPORTS = (('modernization-report.md', 'report-md', 'text/markdown; charset=utf-8'),
               ('manual-recommendations.md', 'report-md', 'text/markdown; charset=utf-8'),
               ('refactors-applied.json', 'report-json', 'application/json'),
               ('quality-before.json', 'report-json', 'application/json'),
               ('quality-after.json', 'report-json', 'application/json'))


def do_modernize(job: Job, ctx: Context):
    """Runs on a copy of the execution's candidate.zip; the candidate and the source are never modified."""
    from ..models import Modernization
    from ..modernization.pipeline import run as modernize
    from ..security import safe_extract_zip
    with session() as db:
        mo = db.get(Modernization, job.subject_id)
        ex = db.get(Execution, mo.execution_id)
        profile = db.get(TargetProfile, db.get(Plan, ex.plan_id).profile_id)
        mo.state = 'running'
        db.commit()
        art = db.scalar(select(Artifact).where(Artifact.execution_id == ex.id, Artifact.kind == 'candidate-zip'))
        if art is None:
            raise StepFailure('NO_CANDIDATE', 'La ejecución no tiene candidate.zip')
        set_phase(ctx, 'candidate', 5)
        zp = ctx.workdir / 'candidate.zip'
        zp.write_bytes(ArtifactStore().read(art))  # sha256 verified by the store
        src = ctx.workdir / 'input'
        safe_extract_zip(zp, src)
        import shutil as _sh
        _sh.rmtree(src / '.migration', ignore_errors=True)  # result metadata of the execution, not part of the project
        set_phase(ctx, 'modernize', 10)
        out = ctx.workdir / 'modernization'
        res = modernize(src, out, target=profile.runtime, approved=set(mo.approved), log=lambda m: ctx.emit('info', 'modernize', m), ctx=ctx)
        set_phase(ctx, 'report', 90)
        ids = {}
        for name, kind, ctype in MOD_REPORTS:
            a = _store(db, ex.project_id, kind, name, (out / 'reports' / name).read_text(encoding='utf-8'), ctype, ctx, execution_id=ex.id, commit=False)
            ids[name] = str(a.id)
        patch = ''.join(p.read_text(encoding='utf-8') for p in sorted((out / 'reports' / 'refactors').glob('*.patch')))
        if patch:
            ids['modernization.patch'] = str(_store(db, ex.project_id, 'patch', 'modernization.patch', patch, 'text/x-diff; charset=utf-8', ctx,
                                                    execution_id=ex.id, commit=False).id)
        zip_path = ctx.workdir / 'modernized.zip'
        source.zip_tree(out / 'modernized-project', zip_path)
        ids['modernized.zip'] = str(_store(db, ex.project_id, 'modernized-zip', 'modernized.zip', zip_path.read_bytes(), 'application/zip',
                                           execution_id=ex.id, commit=False).id)
        counts = {}
        for r in res['results']:
            counts[r['status']] = counts.get(r['status'], 0) + 1
        mo.results, mo.quality_before, mo.quality_after = res['details'], res['qualityBefore'], res['qualityAfter']
        mo.summary = {'baseline': res['baseline'], 'runtime': res['runtime'], 'byStatus': counts, 'artifacts': ids,
                      'note': 'Refactors aplicados sobre una copia del candidato; el candidato de la ejecución no cambia.'}
        db.commit()
    if res['baseline'] != 'PASS':
        return ('blocked', 'BASELINE_NOT_GREEN', 'El candidato no compila o sus pruebas fallan: ningún refactor se aplicó')
    pending = counts.get('PROPOSED_ONLY', 0) + counts.get('BLOCKED', 0) + counts.get('ROLLED_BACK', 0)
    return ('partial_success', 'PROPOSALS_PENDING', f'{pending} regla(s) propuestas, bloqueadas o revertidas') if pending else None


def _package_result(db, ex, ctx, src, repo, profile, plan, evaluated, patch):
    """Doc 19 part B: the final candidate as candidate.zip (served as migrated-project.zip) with .migration/ inside."""
    from ..output import result
    vals = {(v.suite, v.target): v for v in db.scalars(select(Validation).where(Validation.execution_id == ex.id).order_by(Validation.created_at))}
    status = ex.summary.get('status')
    manual = [s for s in evaluated if s['kind'] == 'manual' and s['state'] in ('blocked', 'pending')]
    open_blocking = list(db.scalars(select(Finding).where(Finding.scan_id == plan.scan_id, Finding.blocking.is_(True),
                                                          Finding.status.not_in(['resolved', 'discarded']))))
    report_md = '\n'.join(['# Reporte de migración', '', f"Migration status: {status}", f"Motivo principal: {ex.summary.get('primaryReason') or '—'}",
                           f"Códigos: {', '.join(ex.summary.get('reasonCodes') or []) or '—'}", '', '## Pasos', '']
                          + [f"- {st['key']}: {st['state']}" for st in ex.steps] + ['', '## Validaciones', '']
                          + [f'- {k[1]}/{k[0]}: {v.status}' + (f' — {v.detail[:200]}' if v.detail else '') for k, v in vals.items()]
                          + ['', 'Compilar y pasar pruebas no demuestra equivalencia funcional (gate G4).']) + '\n'
    manual_md = '\n'.join(['# Acciones manuales', ''] + [f"- [{s['state']}] {s['title']} ({s['key']})" for s in manual]
                           + [f'- [hallazgo bloqueante] {f.rule_id} {f.file}:{f.line} — {f.recommendation[:200]}' for f in open_blocking]
                           + ([] if manual or open_blocking else ['Ninguna.'])) + '\n'
    man = result.manifest(status=status, source=repo.source_type,
                          target={'java': profile.java_version, 'camel': profile.camel_version,
                                  ('springBoot' if profile.runtime == 'spring' else 'quarkus'): profile.runtime_version},
                          projects=1, build_passed=getattr(vals.get(('compile', 'candidate')), 'status', None) == 'PASS',
                          tests_passed=getattr(vals.get(('test', 'candidate')), 'status', None) == 'PASS',
                          manual_actions=len(manual) + len(open_blocking),
                          extra={'executionId': str(ex.id), 'baseSnapshot': ex.base_snapshot, 'baseCommit': ex.base_sha})
    data = result.build_zip(src, None, man, {'migration-report.md': report_md, 'manual-actions.md': manual_md, 'diff.patch': patch})
    _store(db, ex.project_id, 'candidate-zip', 'candidate.zip', data, 'application/zip', execution_id=ex.id, commit=False)
    _store(db, ex.project_id, 'report-md', 'manual-actions.md', manual_md, 'text/markdown; charset=utf-8', ctx, execution_id=ex.id, commit=False)
    db.commit()


WS_STATE = {'SUCCEEDED': 'succeeded', 'PARTIAL_SUCCESS': 'partial_success', 'FAILED': 'failed', 'BLOCKED': 'blocked'}


def _restore_previous(db, prev, out: Path):
    """Retry/resume (doc 20 §20): rebuild workspace-state.json and the migrated projects of a previous run from its
    artifacts, so successful projects are reused (never repeated) and their artifacts reach the dependents' overlays."""
    from ..security import safe_extract_zip
    state = {}
    for name, r in (prev.results or {}).items():
        result = {k: v for k, v in r.items() if k not in ('artifacts', 'analysis')}
        state[name] = {'analysis': r.get('analysis'), 'migration': r.get('status'), 'result': result}
        zid = (r.get('artifacts') or {}).get('migrated-project.zip')
        if r.get('status') in ('SUCCEEDED', 'PARTIAL_SUCCESS') and zid:
            art = db.get(Artifact, uuid.UUID(zid))
            z = out / '.prev' / f'{name}.zip'
            z.parent.mkdir(parents=True, exist_ok=True)
            z.write_bytes(ArtifactStore().read(art))
            dest = out / 'projects' / name / 'migrated-project'
            safe_extract_zip(z, dest)
            shutil.rmtree(dest / '.migration', ignore_errors=True)
            rep = out / 'projects' / name / 'reports'
            rep.mkdir(parents=True, exist_ok=True)
            (rep / 'migration-report.json').write_text(json.dumps(result | {'reused': True}))
    (out / 'workspace').mkdir(parents=True, exist_ok=True)
    (out / 'workspace' / 'workspace-state.json').write_text(json.dumps({'projects': state}))
    shutil.rmtree(out / '.prev', ignore_errors=True)


def do_workspace_migrate(job: Job, ctx: Context):
    """Every project of a workspace by analysis waves then migration waves (doc 20), with the same engine as
    `migrate.py migrate --workspace`, on a copy of the workspace scan's snapshot. Inherits this job's deadline,
    cancellation and live log (cli_tool.engine_context). A retry reuses the successful projects of `retry_of`."""
    from .. import cli_tool
    from ..models import WorkspaceMigration
    with session() as db:
        wm = db.get(WorkspaceMigration, job.subject_id)
        scan = db.get(Scan, wm.scan_id)
        profile = db.get(TargetProfile, wm.profile_id)
        wm.state = 'running'
        db.commit()
        snap_art = db.get(Artifact, scan.snapshot_artifact_id) if scan.snapshot_artifact_id else None
        if snap_art is None:
            raise StepFailure('SNAPSHOT_MISSING', 'El análisis de workspace no tiene snapshot; repita el análisis')
        set_phase(ctx, 'snapshot', 3)
        snap_file = ctx.workdir / 'snapshot.tar.gz'
        snap_file.write_bytes(ArtifactStore().read(snap_art))  # the analysed workspace, not whatever the source is now
        src = ctx.workdir / 'workspace'
        source.restore_snapshot(snap_file, src, scan.snapshot_hash)
        out = ctx.workdir / 'ws-out'
        prev = db.get(WorkspaceMigration, wm.retry_of) if wm.retry_of else None
        if prev is not None:
            set_phase(ctx, 'restore-previous', 4)
            _restore_previous(db, prev, out)
        set_phase(ctx, 'waves', 5)
        cli_tool._PARENT['ctx'] = ctx
        try:
            code = cli_tool.migrate_workspace(src, out, profile.runtime, wm.java, dict(wm.accepted), log=lambda m: ctx.emit('info', 'workspace', m),
                                              resume=prev is not None and not wm.retry, retry=tuple(wm.retry or ()),
                                              dry_run=False, autofix=True, max_rounds=5, baseline=True, allow_baseline_failure=False,
                                              auto_up_to='REVIEW', approved=set(wm.approved))
        except cli_tool.InvalidInput as e:
            raise StepFailure('WORKSPACE_INVALID', str(e)) from None
        finally:
            cli_tool._PARENT['ctx'] = None
        set_phase(ctx, 'artifacts', 92)
        wdir = out / 'workspace'
        st = json.loads((wdir / 'workspace-state.json').read_text())
        arts = {}

        def keep(path, name, kind, ctype):
            if path.exists():
                data = path.read_bytes() if ctype == 'application/zip' else redact(path.read_text(errors='replace'), ctx.secrets)
                arts[name] = str(_store(db, wm.project_id, kind, name, data, ctype, ctx if ctype != 'application/zip' else None,
                                        scan_id=scan.id, commit=False).id)
                return arts[name]
            return None
        tag = str(wm.id)[:8]
        md, js = 'text/markdown; charset=utf-8', 'application/json'
        keep(out / 'migrated-workspace.zip', f'ws-{tag}.migrated-workspace.zip', 'candidate-zip', 'application/zip')
        for f, kind, ctype in (('workspace-migration.md', 'report-md', md), ('workspace-analysis.md', 'report-md', md), ('workspace.json', 'report-json', js),
                               ('analysis-waves.json', 'report-json', js), ('migration-waves.json', 'report-json', js), ('workspace-state.json', 'report-json', js)):
            keep(wdir / f, f'ws-{tag}.{f}', kind, ctype)
        workspace_arts = dict(arts)
        results = {}
        for name, x in st['projects'].items():
            pdir = out / 'projects' / name
            pa = {}
            for rel, kind, ctype in (('reports/migration-report.md', 'report-md', md), ('reports/manual-actions.md', 'report-md', md),
                                     ('reports/candidate.patch', 'patch', 'text/x-diff; charset=utf-8'),
                                     ('reports/camel-component-migration-report.md', 'report-md', md), ('analysis/report.md', 'report-md', md),
                                     ('migrated-project.zip', 'candidate-zip', 'application/zip')):
                label = 'analysis-report.md' if rel == 'analysis/report.md' else rel.split('/')[-1]
                aid = keep(pdir / rel, f'ws-{tag}.{name}.{label}', kind, ctype)
                if aid:
                    pa[label] = aid
            prev_arts = ((prev.results or {}).get(name) or {}).get('artifacts', {}) if prev is not None else {}
            if (x.get('result') or {}).get('reused'):  # reused project: its downloads are those of the run that migrated it
                pa = prev_arts | {k: v for k, v in pa.items() if k == 'analysis-report.md'}
            results[name] = (x.get('result') or {'status': x.get('migration')}) | {'analysis': x.get('analysis'), 'artifacts': pa}
        ws = json.loads((wdir / 'workspace.json').read_text())
        wm.results = results
        wm.summary = {'workspaceStatus': st['workspaceStatus'], 'analysisStatus': ws['analysisStatus'], 'migrationStatus': ws['migrationStatus'],
                      'counts': ws['counts'], 'analysisWaves': json.loads((wdir / 'analysis-waves.json').read_text()),
                      'migrationWaves': json.loads((wdir / 'migration-waves.json').read_text()), 'artifacts': workspace_arts, 'exitCode': code,
                      'retryOf': str(wm.retry_of) if wm.retry_of else None}
        db.commit()
    status = st['workspaceStatus']
    ctx.emit('info' if status == 'SUCCEEDED' else 'warn', 'status', f"Workspace: {status} · {ws['analysisStatus']} · {ws['migrationStatus']}")
    return None if status == 'SUCCEEDED' else (WS_STATE.get(status, 'failed'), ws['migrationStatus'],
                                              ', '.join(f"{n}={r['status']}" for n, r in results.items()))


MANDATORY_KINDS = ('recipe',)  # migration recipes; located edits (transform) are optional


# camel-correcciones-compatibilidad 30: root-cause priority (a warning never displaces a technical cause)
ROOT_CAUSE_ORDER = ('ORCHESTRATION_ERROR', 'CAMEL_2_TO_3_DECISION_REQUIRED', 'POM_SANITY_ERROR', 'CAMEL_VERSION_CONFLICT',
                    'ARCHITECTURAL_MIGRATION_REQUIRED', 'INVALID_CAMEL_COMPONENT_MAPPING', 'REMOVED_CAMEL_COMPONENT', 'ARTIFACT_REPLACEMENT_REQUIRED',
                    'UNSUPPORTED_TARGET_ARTIFACT', 'DEPENDENCY_VALIDATION_FAILED', 'DEPENDENCY_RESOLUTION_ERROR', 'JAVA_COMPILATION_ERROR',
                    'TEST_FAILURE')


def build_root_causes(compile_status, test_status, classified_errors, java_errors) -> list[str]:
    """Technical cause of a failed build (instead of the generic CANDIDATE_COMPILE_FAILED / REQUIRED_TESTS_FAILED)."""
    cats = {e['category'] for e in classified_errors}
    out = ['ORCHESTRATION_ERROR'] if 'ORCHESTRATION_ERROR' in cats else []
    if compile_status == 'FAIL':
        if java_errors:
            out.append('JAVA_COMPILATION_ERROR')
        elif 'DEPENDENCY' in cats:
            out.append('DEPENDENCY_RESOLUTION_ERROR')
    elif compile_status == 'PASS' and test_status == 'FAIL':
        out.append('TEST_FAILURE')
    return out


def aggregate_status(steps, vals, plan_blocked=(), manual_actions=0, leading=(), not_executable=False):
    """Doc 16 §9: final state derived from every phase. SKIPPED/NOT_RUN never count as success.
    leading: root causes known before the build (doc 19: DEPENDENCY_VALIDATION_FAILED, INVALID_CAMEL_COMPONENT_MAPPING, ...),
    reported first instead of their consequence (MANDATORY_RECIPE_NOT_APPLIED, CANDIDATE_COMPILE_FAILED)."""
    rank = {c: i for i, c in enumerate(ROOT_CAUSE_ORDER)}
    leading = sorted(dict.fromkeys(leading), key=lambda c: rank.get(c, len(rank)))
    codes = list(leading)
    if vals.get(('maven-model', 'candidate')) not in (None, 'PASS'):
        codes.append('CANDIDATE_MAVEN_MODEL_INVALID')
    mandatory_failed = [s['key'] for s in steps if s.get('kind') in MANDATORY_KINDS and s['state'] in ('failed-rolled-back', 'blocked-by-baseline')]
    mandatory_missing = [s['key'] for s in steps if s.get('kind') in MANDATORY_KINDS and s['state'] == 'skipped']
    if mandatory_failed:
        codes.append('MANDATORY_RECIPE_FAILED')
    if mandatory_missing:
        codes.append('MANDATORY_RECIPE_NOT_APPLIED')
    warnings = []
    compile_v, test_v = vals.get(('compile', 'candidate')), vals.get(('test', 'candidate'))
    if not_executable and (compile_v in ('NOT_EXECUTABLE', 'NOT_RUN') or (compile_v == 'PASS' and test_v in ('NOT_EXECUTABLE', 'NOT_RUN'))):
        warnings += ['MIGRATION_DEGRADED', 'BUILD_NOT_EXECUTABLE']  # doc 32: environment, not code: never FAILED for it
    else:
        if vals.get(('compile', 'candidate')) != 'PASS':
            codes.append('CANDIDATE_COMPILE_FAILED')
        test = vals.get(('test', 'candidate'))
        if test == 'SKIPPED':
            codes.append('SKIPPED_DUE_TO_COMPILE_FAILURE')
        elif test != 'PASS':
            codes.append('REQUIRED_TESTS_FAILED')
    if vals.get(('secret-scan', 'candidate')) == 'FINDINGS':
        warnings.append('SECRET_FINDINGS')
    if vals.get(('secret-scan', 'candidate')) == 'ERROR':
        warnings.append('SECRET_SCANNER_ERROR')
    if any(s.get('kind') == 'transform' and s['state'] not in ('applied', 'no-changes') for s in steps):
        warnings.append('OPTIONAL_STEP_NOT_APPLIED')
    if plan_blocked or manual_actions:  # doc 16 §10: manual tasks pending -> never plain SUCCEEDED
        warnings.append('MANUAL_ACTIONS_PENDING')
    order = list(dict.fromkeys(leading)) + ['CANDIDATE_MAVEN_MODEL_INVALID', 'MANDATORY_RECIPE_FAILED', 'MANDATORY_RECIPE_NOT_APPLIED', 'CANDIDATE_COMPILE_FAILED',
             'REQUIRED_TESTS_FAILED', 'SKIPPED_DUE_TO_COMPILE_FAILURE']
    if codes:
        status = 'FAILED'
    elif warnings:
        status = 'PARTIAL_SUCCESS'
    else:
        status = 'SUCCEEDED'
    primary = next((c for c in order if c in codes), warnings[0] if warnings else None)
    return {'status': status, 'primaryReason': primary, 'reasonCodes': codes + warnings,
            'mandatoryFailed': mandatory_failed, 'mandatoryNotApplied': mandatory_missing}


def _report(db, ex, plan, profile, ctx):
    vals = list(db.scalars(select(Validation).where(Validation.execution_id == ex.id)))
    files = list(db.scalars(select(DiffFile).where(DiffFile.execution_id == ex.id)))
    data = {'executionId': str(ex.id), 'mode': ex.mode, 'baseCommit': ex.base_sha, 'baseSnapshot': ex.base_snapshot,
            'plan': {'id': str(plan.id), 'version': plan.version, 'digest': plan.digest},
            'profile': {'key': profile.key, 'version': profile.version, 'digest': profile.digest, 'tooling': profile.tooling},
            'steps': ex.steps, 'summary': ex.summary,
            'validations': [{'suite': v.suite, 'target': v.target, 'gate': v.gate, 'status': v.status, 'exitCode': v.exit_code,
                             'tests': v.tests, 'detail': v.detail} for v in vals],
            'diff': [{'path': f.path, 'change': f.change_type, 'additions': f.additions, 'deletions': f.deletions, 'recipes': f.recipes} for f in files],
            'gates': gates.evaluate(db, ex),
            'limitations': ['El origen (Git, carpeta o ZIP) no se modificó; el candidato se entrega como candidate.zip y candidate.patch.',
                            'Compilación y pruebas unitarias no demuestran equivalencia funcional.',
                            'Los pasos manuales/bloqueados no se aplicaron.']}
    esc = lambda x: html.escape('' if x is None else str(x))
    rows = lambda items, keys: ''.join('<tr>' + ''.join(f'<td>{esc(i.get(k))}</td>' for k in keys) + '</tr>' for i in items)
    page = ('<!doctype html><html lang="es"><meta charset="utf-8"><title>Reporte de ejecución</title>'
            '<style>body{font:15px system-ui;margin:32px;color:#153046}table{border-collapse:collapse;width:100%;margin-bottom:24px}'
            'td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}</style>'
            f'<h1>Ejecución {esc(ex.mode)} {esc(ex.id)}</h1><p>Snapshot base {esc(ex.base_snapshot)}' + (f' (commit {esc(ex.base_sha)})' if ex.base_sha else '') + '. '
            f'Perfil {esc(profile.name)} v{profile.version}.</p>'
            '<h2>Pasos</h2><table><tr><th>Paso</th><th>Estado</th><th>Commit</th></tr>' + rows(ex.steps, ('key', 'state', 'commit')) + '</table>'
            '<h2>Validaciones</h2><table><tr><th>Suite</th><th>Objetivo</th><th>Gate</th><th>Estado</th><th>Detalle</th></tr>'
            + rows(data['validations'], ('suite', 'target', 'gate', 'status', 'detail')) + '</table>'
            '<h2>Gates</h2><table><tr><th>Gate</th><th>Título</th><th>Estado</th><th>Obligatorio</th></tr>'
            + rows(data['gates'], ('gate', 'title', 'status', 'required')) + '</table>'
            '<h2>Archivos</h2><table><tr><th>Archivo</th><th>Cambio</th><th>+</th><th>-</th></tr>'
            + rows(data['diff'], ('path', 'change', 'additions', 'deletions')) + '</table>'
            '<h2>Limitaciones</h2><ul>' + ''.join(f'<li>{esc(l)}</li>' for l in data['limitations']) + '</ul></html>')
    _store(db, ex.project_id, 'report-json', 'execution-report.json', json.dumps(data, ensure_ascii=False, indent=2, default=str), 'application/json', ctx, execution_id=ex.id)
    _store(db, ex.project_id, 'report-html', 'execution-report.html', page, 'text/html; charset=utf-8', ctx, execution_id=ex.id)
    db.commit()


# --- draft pull request -------------------------------------------------------------------------
def do_pull_request(job: Job, ctx: Context):
    from .providers import create_draft_pr
    with session() as db:
        ex = db.get(Execution, job.subject_id)
        project = db.get(Project, ex.project_id)
        blockers = gates.pr_blockers(db, ex)
        if blockers:  # re-checked at run time
            raise StepFailure('GATES_NOT_MET', '; '.join(blockers))
        repo = db.get(Repository, project.repository_id)
        if repo.source_type != 'git':
            raise StepFailure('PR_REQUIRES_GIT', 'Ramas y PR solo existen para orígenes Git')
        patch_art = db.scalar(select(Artifact).where(Artifact.execution_id == ex.id, Artifact.kind == 'patch', Artifact.name == 'candidate.patch'))
        scan = db.get(Scan, db.get(Plan, ex.plan_id).scan_id)
        if patch_art is None:
            raise StepFailure('NO_CANDIDATE', 'La ejecución no produjo cambios')
        cfg = project.pr_config
        try:
            token = resolve_credential(cfg['credentialRef'], CREDENTIALS or None)
        except UnsafeInput as e:
            raise StepFailure('CREDENTIAL_UNAVAILABLE', str(e)) from None
        if not token:
            raise StepFailure('CREDENTIAL_UNAVAILABLE', 'Credencial de PR no disponible en el worker')
        set_phase(ctx, 'checkout', 20)
        src = ctx.workdir / 'pr'
        gitops.checkout(ctx, repo.url, ex.base_sha, src, token)
        ppath = ctx.workdir / 'candidate.patch'
        ppath.write_bytes(ArtifactStore().read(patch_art))
        gitops.apply_patch(ctx, src, ppath, scan.project_root)
        gitops.commit_all(ctx, src, f'Migration Factory: candidato {str(ex.id)[:8]}')
        branch = f'mf/{str(ex.id)[:8]}'
        set_phase(ctx, 'push', 60)
        gitops.push_branch(ctx, src, repo.url, branch, token)
        set_phase(ctx, 'pull-request', 80)
        url = create_draft_pr(cfg, branch, title=f'[Migration Factory] Candidato {str(ex.id)[:8]} (borrador)',
                              body=f'Candidato generado por Migration Factory.\n\nCommit base: {ex.base_sha}\nSnapshot: {ex.base_snapshot}\n\n'
                                   'Borrador: requiere revisión humana. Compilación/pruebas no demuestran equivalencia funcional.',
                              token=token)
        ex.pr_url = url
        db.commit()
        ctx.emit('info', 'pull-request', f'PR borrador creado: {url}')

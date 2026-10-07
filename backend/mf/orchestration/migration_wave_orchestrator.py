"""MigrationWaveOrchestrator (20-CORRECCION §9-§21): migration by waves of the internal dependency graph.

Per wave: projects whose internal dependencies did not migrate are BLOCKED (BLOCKED_BY_DEPENDENCY, `blockedBy`), the rest
are RUNNABLE and migrate (in parallel when configured). A failure never cancels the wave or the workspace; a project
that was never attempted is BLOCKED, never FAILED. With previous results (resume/retry) successful projects are reused,
not repeated, and blocked dependents are re-evaluated (BLOCKED -> READY)."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

OK = ('SUCCEEDED', 'PARTIAL_SUCCESS')


def wave_status(runnable_results: list[str], blocked: int) -> str:
    if not runnable_results:
        return 'WAVE_BLOCKED' if blocked else 'WAVE_SUCCEEDED'
    ok = [s for s in runnable_results if s in OK]
    if len(ok) == len(runnable_results) and not blocked:
        return 'WAVE_SUCCEEDED'
    if not ok:
        return 'WAVE_FAILED'
    return 'WAVE_PARTIAL_SUCCESS'


def migration_status(results: dict) -> str:
    states = [r['status'] for r in results.values()]
    if states and all(s == 'SUCCEEDED' for s in states):
        return 'MIGRATION_SUCCEEDED'
    if any(s in OK for s in states):
        return 'MIGRATION_PARTIAL_SUCCESS'
    if states and all(s == 'BLOCKED' for s in states):
        return 'MIGRATION_BLOCKED'
    return 'MIGRATION_FAILED'


def workspace_status(analysis: str, migration: str | None) -> str:
    if migration is None:
        return {'ANALYSIS_SUCCEEDED': 'SUCCEEDED', 'ANALYSIS_FAILED': 'FAILED'}.get(analysis, 'PARTIAL_SUCCESS')
    m = migration.replace('MIGRATION_', '')
    return 'PARTIAL_SUCCESS' if m == 'SUCCEEDED' and analysis != 'ANALYSIS_SUCCEEDED' else m


def run(ws, migrate_fn, *, parallelism: int | None = None, log=print, previous: dict | None = None, rerun: set | None = None,
        on_reused=None):
    """migrate_fn(project, wave_label) -> {'status', 'reason', ...}. previous: project -> last result (resume/retry);
    rerun: projects to retry (None = every project that did not succeed). Returns (results, waves)."""
    parallelism = parallelism or int(os.environ.get('MF_WORKSPACE_MIGRATION_PARALLELISM', '1'))
    by = {p.name: p for p in ws.projects}
    prev = previous or {}
    results = {}
    for n, why in ws.order['blocked'].items():  # cycles: only these projects (and their dependents) leave the automatic order
        cyc = next((c['projects'] for c in ws.order['cycles'] if n in c['projects']), [])
        results[n] = {'project': n, 'status': 'BLOCKED', 'reason': why, 'blockedBy': [d for d in by[n].dependencies if d in ws.order['blocked']] or cyc}
        by[n].migration_status = 'BLOCKED'
    waves = []
    for i, w in enumerate(ws.order['waves'], 1):
        label = f'wave-{i}'
        runnable, blocked, reused = [], [], []
        for name in w['projects']:
            p, last = by[name], prev.get(name)
            if last and last.get('status') in OK and (rerun is None or name not in rerun):
                results[name] = last | {'reused': True}  # already migrated: never repeated
                p.migration_status = 'PASS' if last['status'] == 'SUCCEEDED' else 'WARN'
                reused.append(name)
                if on_reused:
                    on_reused(p)
                continue
            if last and last.get('status') == 'FAILED' and rerun is not None and name not in rerun:
                results[name] = last | {'reused': True}  # a failure is only retried when asked
                p.migration_status = 'FAIL'
                reused.append(name)
                continue
            # a dependency counts only if it migrated AND its artifact can be built (doc 32 §21: degraded without artifact)
            failed = [d for d in p.dependencies if results.get(d, {}).get('status') not in OK or results.get(d, {}).get('artifactAvailable') is False]
            if failed:
                results[name] = {'project': name, 'status': 'BLOCKED', 'reason': 'BLOCKED_BY_DEPENDENCY', 'blockedBy': failed}
                p.migration_status = 'BLOCKED'
                log(f'[workspace][migration-{label}][{name}][migration] BLOCKED_BY_DEPENDENCY ({", ".join(failed)})')
                blocked.append(name)
            else:
                runnable.append(p)
        for p in runnable:
            p.migration_status = 'RUNNING'
        with ThreadPoolExecutor(max_workers=max(1, parallelism)) as pool:
            futures = {pool.submit(migrate_fn, p, label): p for p in runnable}
            for f in as_completed(futures):
                p = futures[f]
                try:
                    r = f.result()
                except Exception as e:  # noqa: BLE001 — one project's crash stays in that project
                    r = {'status': 'FAILED', 'reason': f'PROJECT_MIGRATION_ERROR: {type(e).__name__}: {str(e)[:200]}'}
                results[p.name] = {'project': p.name} | r
                p.migration_status = {'SUCCEEDED': 'PASS', 'PARTIAL_SUCCESS': 'WARN', 'BLOCKED': 'BLOCKED'}.get(r['status'], 'FAIL')
                log(f"[workspace][migration-{label}][{p.name}][migration] {r['status']}")
        waves.append({'wave': f'M{i}', 'projects': list(w['projects']), 'runnable': [p.name for p in runnable], 'blocked': blocked,
                      'reused': reused, 'status': wave_status([results[p.name]['status'] for p in runnable], len(blocked)),
                      'results': {n: results[n]['status'] for n in w['projects']}})
    ws.migration_status = migration_status(results)
    ws.migration_waves = waves
    return results, waves


def state(ws, results: dict) -> dict:
    """Persisted per project (doc 20 §21): enables resume and retry."""
    from .analysis_wave_orchestrator import OUTCOME
    return {p.name: {'analysis': OUTCOME.get(p.analysis_status, p.analysis_status), 'migration': results.get(p.name, {}).get('status', 'PENDING'),
                     'result': results.get(p.name)} for p in ws.projects}

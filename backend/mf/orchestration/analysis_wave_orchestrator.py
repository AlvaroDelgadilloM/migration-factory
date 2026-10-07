"""AnalysisWaveOrchestrator (20-CORRECCION §4-§8): analysis by waves of the internal dependency graph.

Projects of a wave run in parallel; after each wave its findings are propagated to the dependents as `dependencyContext`.
A failed analysis never blocks the local analysis of a dependent: the dependent is analysed with partial context
(PARTIAL). Projects in a dependency cycle are analysed in a final wave, also with partial context."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

OK = ('PASS', 'WARN')
OUTCOME = {'PASS': 'ANALYSIS_SUCCEEDED', 'WARN': 'ANALYSIS_SUCCEEDED', 'PARTIAL': 'ANALYSIS_PARTIAL', 'FAIL': 'ANALYSIS_FAILED',
           'BLOCKED': 'ANALYSIS_BLOCKED', 'PENDING': 'ANALYSIS_PENDING', 'RUNNING': 'ANALYSIS_RUNNING'}


def analysis_waves(ws) -> list[dict]:
    waves = [{'wave': f"A{w['wave']}", 'projects': list(w['projects']), 'status': 'ANALYSIS_WAVE_PENDING'} for w in ws.order['waves']]
    cyclic = sorted(ws.order['blocked'])
    if cyclic:  # still analysed: their code can be reviewed even if the migration order cannot be automatic
        waves.append({'wave': f'A{len(waves) + 1}', 'projects': cyclic, 'status': 'ANALYSIS_WAVE_PENDING', 'note': 'proyectos en ciclo o dependientes de un ciclo'})
    return waves


def wave_status(states: list[str]) -> str:
    if states and all(s in OK for s in states):
        return 'ANALYSIS_WAVE_SUCCEEDED'
    if states and all(s == 'FAIL' for s in states):
        return 'ANALYSIS_WAVE_FAILED'
    return 'ANALYSIS_WAVE_PARTIAL_SUCCESS'


def run(ws, analyze_fn, *, workers: int | None = None, log=print):
    workers = workers or int(os.environ.get('MF_WORKSPACE_PARALLELISM', '4'))
    by = {p.name: p for p in ws.projects}
    waves = analysis_waves(ws)

    def one(p, label):
        ctx = [{'project': d, 'analysis': OUTCOME.get(by[d].analysis_status, by[d].analysis_status),
                'findings': sorted({f['ruleId'] for f in by[d].findings})} for d in p.dependencies]
        p.dependency_context = ctx
        p.analysis_status = 'RUNNING'
        log(f'[workspace][analysis-wave-{label}][{p.name}][analysis] START')
        try:
            r = analyze_fn(p)  # the explicit ProjectContext, never a shared "current project"
        except Exception as e:  # noqa: BLE001 — isolated: the rest of the wave and the next waves continue
            p.analysis_status, p.error = 'FAIL', f'PROJECT_ANALYSIS_FAILED: {type(e).__name__}: {str(e)[:300]}'
            return p
        r['dependencyContext'] = ctx
        p.report, p.findings, p.integrations = r, r.get('findings', []), r.get('integrations', [])
        from .multi_project_orchestrator import _technologies
        p.technologies = _technologies(r)
        p.context_missing = [d for d in p.dependencies if by[d].analysis_status not in OK + ('PARTIAL',)]
        platform_blocked = (r.get('baseline') or {}).get('status') == 'BASELINE_BLOCKED_PLATFORM_BOM'  # doc 42 §14/§18
        p.analysis_status = 'PARTIAL' if p.context_missing or platform_blocked else ('WARN' if any(f.get('blocking') for f in p.findings) or r.get('errors') else 'PASS')
        return p
    for i, wave in enumerate(waves, 1):
        wave['status'] = 'ANALYSIS_WAVE_RUNNING'
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(one, by[n], i): n for n in wave['projects']}
            for f in as_completed(futures):
                p = f.result()
                log(f'[workspace][analysis-wave-{i}][{p.name}][analysis] {p.analysis_status}' + (f' ({p.error})' if p.error else '')
                    + (f" contexto incompleto: {', '.join(p.context_missing)}" if getattr(p, 'context_missing', None) else ''))
        wave['status'] = wave_status([by[n].analysis_status for n in wave['projects']])
        wave['results'] = {n: OUTCOME[by[n].analysis_status] for n in wave['projects']}
    states = [p.analysis_status for p in ws.projects]
    ws.analysis_waves = waves
    ws.analysis_status = ('ANALYSIS_SUCCEEDED' if states and all(s in OK for s in states)
                          else 'ANALYSIS_FAILED' if all(s == 'FAIL' for s in states) else 'ANALYSIS_PARTIAL_SUCCESS')
    return ws

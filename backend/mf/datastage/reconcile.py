"""Reconciliation evidence (doc 60): the reconciliation-result.json that run_reconciliation.py writes in the client's
environment (DataStage output vs Airflow output, same input) is uploaded to the migration and evaluated here.

The verdicts are recomputed from the values, never taken from the file: a table is EQUAL only when every check has the
same value on both sides and the difference sample is empty. A job is VERIFIED when all its tables are EQUAL; the jobs
that write no Oracle table, or that the upload does not cover, stay NOT_RECONCILED."""
from __future__ import annotations

from .generation import RECON_FORMAT

MAX_TABLES, MAX_CHECKS, MAX_SAMPLE, MAX_TEXT = 2000, 1000, 50, 300
DIFF_KINDS = ('onlyLegacy', 'onlyTarget', 'changed')


class InvalidResult(ValueError):
    pass


def _text(v, field):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        raise InvalidResult(f'{field}: valor no válido')
    return str(v)[:MAX_TEXT]


def _table(t: dict, jobs: set[str]) -> dict:
    if not isinstance(t, dict) or not isinstance(t.get('job'), str) or not isinstance(t.get('table'), str):
        raise InvalidResult('cada tabla necesita job y table')
    if t['job'] not in jobs:
        raise InvalidResult(f'el job {t["job"][:80]} no pertenece a esta migración')
    out = {'job': t['job'], 'table': t['table'][:128], 'stage': _text(t.get('stage'), 'stage'),
           'excluded': [_text(c, 'excluded') for c in (t.get('excluded') or [])][:MAX_CHECKS]}
    if t.get('error'):
        return out | {'verdict': 'ERROR', 'error': _text(t['error'], 'error'), 'checks': [], 'diff': {}, 'differences': 0}
    checks = t.get('checks')
    if not isinstance(checks, list) or not checks or len(checks) > MAX_CHECKS:
        raise InvalidResult(f'{t["table"][:80]}: sin chequeos')
    out['checks'] = []
    for c in checks:
        if not isinstance(c, dict) or not isinstance(c.get('name'), str):
            raise InvalidResult(f'{t["table"][:80]}: chequeo no válido')
        legacy, target = _text(c.get('legacy'), 'legacy'), _text(c.get('target'), 'target')
        out['checks'].append({'name': c['name'][:120], 'legacy': legacy, 'target': target, 'equal': legacy == target})
    diff = t.get('diff') or {}
    if not isinstance(diff, dict):
        raise InvalidResult(f'{t["table"][:80]}: diff no válido')
    out['diff'] = {k: [_text(x, k) for x in (diff.get(k) or [])][:MAX_SAMPLE] for k in DIFF_KINDS} | {'truncated': bool(diff.get('truncated'))}
    out['differences'] = sum(not c['equal'] for c in out['checks']) + sum(len(out['diff'][k]) for k in DIFF_KINDS)
    out['verdict'] = 'EQUAL' if out['differences'] == 0 else 'DIFFERENT'
    return out


def evaluate(result: dict, jobs: set[str]) -> dict:
    """The stored evaluation of one upload; `jobs` are the jobs of the migration (an upload of another project is refused)."""
    if not isinstance(result, dict) or result.get('format') != RECON_FORMAT:
        raise InvalidResult(f'no es un reconciliation-result.json ({RECON_FORMAT}) generado por run_reconciliation.py')
    tables = result.get('tables')
    if not isinstance(tables, list) or not tables or len(tables) > MAX_TABLES:
        raise InvalidResult('el resultado no trae tablas')
    evaluated = [_table(t, jobs) for t in tables]
    by_job: dict[str, list] = {}
    for t in evaluated:
        by_job.setdefault(t['job'], []).append(t['verdict'])
    verdict = {j: 'ERROR' if 'ERROR' in v else 'DIFFERENT' if 'DIFFERENT' in v else 'VERIFIED' for j, v in by_job.items()}
    sides = {k: {f: _text((result.get(k) or {}).get(f), f) for f in ('conn', 'schema')} for k in ('legacy', 'target')}
    counts = {s: sum(v == s for v in verdict.values()) for s in ('VERIFIED', 'DIFFERENT', 'ERROR')}
    return {'executedAt': _text(result.get('executedAt'), 'executedAt'), 'legacy': sides['legacy'], 'target': sides['target'],
            'tables': evaluated, 'jobs': verdict, 'counts': counts | {'NOT_RECONCILED': len(jobs - set(verdict))},
            'status': 'VERIFIED' if counts['VERIFIED'] and not counts['DIFFERENT'] and not counts['ERROR'] else
                      'DIFFERENT' if counts['DIFFERENT'] else 'ERROR'}

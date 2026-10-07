"""Prometheus metrics (improvements/08), computed from recorded jobs, executions, validations and findings.
Only facts already in the database; a ratio with no data is omitted, never reported as 0 or 100%."""
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import Principal, current_principal, require_admin
from ..db import get_db
from ..models import Execution, Finding, Job, Validation

router = APIRouter()
DONE = ('succeeded', 'partial_success', 'failed', 'blocked', 'timed_out')


def render(db: Session) -> str:
    out = []

    def metric(name, kind, help_, samples):
        out.extend([f'# HELP {name} {help_}', f'# TYPE {name} {kind}'])
        out.extend(f'{name}{lbl} {val}' for lbl, val in samples)

    started = db.execute(select(Job.kind, func.count()).where(Job.started_at.is_not(None)).group_by(Job.kind)).all()
    failed = db.execute(select(Job.kind, func.count()).where(Job.state.in_(('failed', 'timed_out'))).group_by(Job.kind)).all()
    metric('mf_jobs_started_total', 'counter', 'Jobs started, by kind', [(f'{{kind="{k}"}}', n) for k, n in started])
    metric('mf_jobs_failed_total', 'counter', 'Jobs failed or timed out, by kind', [(f'{{kind="{k}"}}', n) for k, n in failed])
    ex = dict(db.execute(select(Execution.state, func.count()).where(Execution.mode == 'apply', Execution.state.in_(DONE))
                         .group_by(Execution.state)).all())
    if sum(ex.values()):
        metric('mf_migration_success_rate', 'gauge', 'Finished apply executions SUCCEEDED or PARTIAL_SUCCESS / finished',
               [('', round((ex.get('succeeded', 0) + ex.get('partial_success', 0)) / sum(ex.values()), 4))])
    avg = db.scalar(select(func.avg(Validation.duration_ms)).where(Validation.suite == 'compile', Validation.duration_ms.is_not(None)))
    if avg is not None:
        metric('mf_average_build_time_seconds', 'gauge', 'Mean duration of compile validations', [('', round(float(avg) / 1000, 3))])
    applied = sum(1 for steps in db.scalars(select(Execution.steps).where(Execution.mode == 'apply'))
                  for s in steps or [] if s.get('kind') == 'recipe' and s.get('state') == 'applied')
    metric('mf_recipes_applied_total', 'counter', 'Recipe steps applied in apply executions', [('', applied)])
    total = db.scalar(select(func.count()).select_from(Finding)) or 0
    if total:
        manual = db.scalar(select(func.count()).select_from(Finding).where(Finding.classification.in_(('MANUAL', 'REVIEW')))) or 0
        metric('mf_manual_review_ratio', 'gauge', 'Findings classified MANUAL or REVIEW / all findings', [('', round(manual / total, 4))])
    # autofix_success_rate: the platform worker does not autofix; the CLI reports autofix per run in migration-report.json
    return '\n'.join(out) + '\n'


@router.get('/metrics', response_class=PlainTextResponse, include_in_schema=False)
def metrics(db: Session = Depends(get_db), me: Principal = Depends(current_principal)):
    require_admin(me)  # organisation-wide numbers
    return PlainTextResponse(render(db), media_type='text/plain; version=0.0.4')

"""MigrationValidationState (code-review 45 + 48): one validation state for CLI, API worker and workspace.

Every path builds it with `build()` from the same facts, so the same execution reports the same state everywhere.
The structural Maven model, the real dependency resolution and the compile are separate facts: a model checked while
stubs were installed is PASS_WITH_STUBS, never plain PASS, and the overall result is never SUCCEEDED from a single
Maven exit code (it needs real resolution, compile and tests, without stubs)."""
from __future__ import annotations

UNVERIFIED = ('NOT_EXECUTABLE', 'NOT_RUN', 'SKIPPED', 'BLOCKED', None)
_STEP = {'applied': 'APPLIED', 'failed-rolled-back': 'FAILED', 'rolled_back': 'FAILED', 'blocked-by-baseline': 'BLOCKED', 'skipped': 'SKIPPED'}


def _recipes(step_states: list[str]) -> str:
    st = [_STEP.get(s, s).upper() for s in step_states]
    if not st:
        return 'NOT_RUN'
    if all(s == 'BLOCKED' or s == 'BLOCKED_BY_BASELINE' for s in st):
        return 'BLOCKED'
    if any(s in ('FAILED', 'ROLLED_BACK') for s in st):
        return 'PARTIAL'
    return 'COMPLETED' if any(s == 'APPLIED' for s in st) else 'NOT_RUN'


def build(*, baseline_model: str | None, baseline_state: str | None, baseline_compile: str | None, step_states: list[str],
          candidate_model: str | None, candidate_compile: str | None, candidate_test: str | None, stubbed: list[str] = (),
          degraded: bool = False, validation_confidence: str | None = None, status: str | None = None,
          primary_reason: str | None = None, reason_codes: list[str] = ()) -> dict:
    stubbed = list(stubbed or [])
    blocked_dep = (baseline_state or '').startswith('BASELINE_BLOCKED_')
    cand_compile = 'UNVERIFIED' if candidate_compile in UNVERIFIED else candidate_compile
    cand_test = 'NOT_RUN' if candidate_test in UNVERIFIED else candidate_test
    if degraded or blocked_dep:
        resolution = 'BLOCKED_EXTERNAL_DEPENDENCY' if (baseline_state or '') in ('BASELINE_BLOCKED_DEPENDENCY', 'BASELINE_BLOCKED_NETWORK',
                                                                                 'BASELINE_BLOCKED_REPOSITORY', 'BASELINE_BLOCKED_AUTH') or degraded \
            else (baseline_state or '').replace('BASELINE_', '')
    else:
        resolution = 'RESOLVED' if cand_compile in ('PASS', 'FAIL') else 'UNVERIFIED'  # javac ran: Maven resolved the classpath
    model = candidate_model
    if model == 'PASS' and stubbed:
        model = 'PASS_WITH_STUBS'
    overall = status
    real = resolution == 'RESOLVED' and cand_compile == 'PASS' and cand_test == 'PASS' and not stubbed
    if overall == 'SUCCEEDED' and not real:  # no green from one auxiliary BUILD SUCCESS
        overall = 'PARTIAL_SUCCESS'
    codes = list(reason_codes or [])
    return {'baselineModel': baseline_model or 'NOT_RUN',
            'baselineDependencyResolution': 'RESOLVED' if baseline_compile == 'PASS' and not blocked_dep else (
                (baseline_state or 'UNKNOWN').replace('BASELINE_', '') if blocked_dep else 'UNVERIFIED'),
            'baselineCompile': baseline_compile or 'NOT_RUN',
            'recipeExecution': _recipes(step_states),
            'candidateModel': model or 'NOT_RUN',
            'candidateModelStructural': candidate_model or 'NOT_RUN',
            'candidateDependencyResolution': resolution,
            'candidateCompile': cand_compile,
            'candidateCompileValidation': 'VERIFIED' if cand_compile == 'PASS' and not stubbed and resolution == 'RESOLVED' else 'UNVERIFIED',
            'candidateTest': cand_test,
            'stubMode': 'DEGRADED_WITH_STUBS' if stubbed else 'NONE',
            'resolutionMode': 'DEGRADED_WITH_STUBS' if stubbed else 'REAL',
            'stubbedDependencies': stubbed,
            'validationConfidence': validation_confidence,
            'primaryReason': primary_reason,
            'secondaryReasons': [c for c in codes if c != primary_reason],
            'overall': overall}


if __name__ == '__main__':  # self-check: the degraded example of doc 48
    s = build(baseline_model='VALID', baseline_state='BASELINE_BLOCKED_DEPENDENCY', baseline_compile='NOT_EXECUTABLE',
              step_states=['applied', 'applied'], candidate_model='PASS', candidate_compile='NOT_EXECUTABLE', candidate_test='NOT_EXECUTABLE',
              stubbed=['com.oracle:ojdbc6:jar:11.2.0.4'], degraded=True, validation_confidence='REDUCED', status='PARTIAL_SUCCESS',
              primary_reason='MIGRATION_DEGRADED', reason_codes=['MIGRATION_DEGRADED', 'BUILD_NOT_EXECUTABLE'])
    assert (s['recipeExecution'], s['candidateModel'], s['candidateDependencyResolution'], s['candidateCompile'], s['candidateTest'],
            s['overall']) == ('COMPLETED', 'PASS_WITH_STUBS', 'BLOCKED_EXTERNAL_DEPENDENCY', 'UNVERIFIED', 'NOT_RUN', 'PARTIAL_SUCCESS')
    assert s['candidateCompileValidation'] == 'UNVERIFIED' and s['secondaryReasons'] == ['BUILD_NOT_EXECUTABLE']
    ok = build(baseline_model='VALID', baseline_state='BASELINE_SUCCESS', baseline_compile='PASS', step_states=['applied'],
               candidate_model='PASS', candidate_compile='PASS', candidate_test='PASS', status='SUCCEEDED')
    assert ok['overall'] == 'SUCCEEDED' and ok['candidateCompileValidation'] == 'VERIFIED' and ok['candidateDependencyResolution'] == 'RESOLVED'
    assert build(baseline_model='VALID', baseline_state='BASELINE_SUCCESS', baseline_compile='PASS', step_states=['applied'],
                 candidate_model='PASS', candidate_compile='PASS', candidate_test='NOT_RUN', status='SUCCEEDED')['overall'] == 'PARTIAL_SUCCESS'
    print('ok')

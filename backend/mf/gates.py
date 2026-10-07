"""Gate evaluation (docs/09_PRUEBAS_SEGURIDAD.md). SKIPPED / NOT_RUN never count as PASS."""
from sqlalchemy import select

from .models import DiffFile, Execution, Finding, Plan, Project, Scan, Validation

GATES = {
    'G0': 'Línea base documentada',
    'G1': 'Perfil y dependencias resueltos',
    'G2': 'Compilación del candidato',
    'G3': 'Pruebas unitarias / contratos',
    'G4': 'JMS / BD / transacciones (evidencia de equivalencia)',
    'G5': 'Permisos y secretos',
    'G6': 'Diff revisado',
}


def evaluate(db, execution: Execution):
    plan = db.get(Plan, execution.plan_id)
    scan = db.get(Scan, plan.scan_id)
    project = db.get(Project, execution.project_id)
    vals = list(db.scalars(select(Validation).where(Validation.execution_id == execution.id).order_by(Validation.created_at)))
    latest = {}
    for v in vals:
        latest[(v.suite, v.target)] = v
    out = {}

    def put(gate, status, detail):
        out[gate] = {'gate': gate, 'title': GATES[gate], 'status': status, 'detail': detail,
                     'required': gate in (project.required_gates or [])}

    bc, bt = latest.get(('compile', 'baseline')), latest.get(('test', 'baseline'))
    if bc and bt and bc.status in ('PASS', 'FAIL') and bt.status in ('PASS', 'FAIL', 'SKIPPED'):
        put('G0', 'PASS', f'Línea base registrada: compile={bc.status}, test={bt.status}')
    else:
        put('G0', 'NOT_RUN', 'Línea base no ejecutada')
    dv = next((latest[(s, 'candidate')] for s in ('pom-sanity', 'camel-compatibility', 'camel-version', 'artifact-availability', 'dependency-validation')
               if latest.get((s, 'candidate')) and latest[(s, 'candidate')].status == 'FAIL'), None)
    if dv:  # docs 19/22/29: a preflight gate failed on the candidate
        put('G1', 'FAIL', f'{dv.suite}: {dv.detail}')
    elif (scan.maven_resolution or '').startswith('maven-effective'):
        put('G1', 'PASS', 'POM efectivo resuelto con Maven en sandbox')
    else:
        put('G1', 'FAIL', f'Resolución Maven: {scan.maven_resolution}')
    if execution.mode != 'apply':
        for g in ('G2', 'G3', 'G5', 'G6'):
            put(g, 'NOT_RUN', 'Solo aplica a ejecuciones (no a preview)')
    else:
        cc, ct = latest.get(('compile', 'candidate')), latest.get(('test', 'candidate'))
        put('G2', cc.status if cc else 'NOT_RUN', cc.detail if cc and cc.detail else '')
        if ct is None:
            put('G3', 'NOT_RUN', '')
        elif ct.status == 'FAIL' and bt is not None and bt.status == 'FAIL':
            put('G3', 'FAIL', 'Fallan también en línea base: no es regresión, pero no se acepta automáticamente')
        else:
            put('G3', ct.status, ct.detail or '')
        ss = latest.get(('secret-scan', 'candidate'))
        put('G5', ss.status if ss else 'NOT_RUN', ss.detail if ss and ss.detail else '')
        files = list(db.scalars(select(DiffFile).where(DiffFile.execution_id == execution.id)))
        if not files:
            put('G6', 'NOT_RUN', 'Sin diff')
        elif any(f.review_status == 'rejected' for f in files):
            put('G6', 'FAIL', 'Hay archivos rechazados')
        elif all(f.review_status == 'approved' for f in files):
            put('G6', 'PASS', f'{len(files)} archivo(s) aprobados')
        else:
            put('G6', 'NOT_RUN', f"{sum(f.review_status == 'pending' for f in files)} archivo(s) pendientes")
    eq = latest.get(('equivalence', 'candidate'))
    put('G4', eq.status if eq else 'NOT_RUN', (eq.detail or '') if eq else 'Requiere evidencia registrada por un revisor')
    return [out[g] for g in sorted(out)]


def pr_blockers(db, execution: Execution):
    """Reasons why a draft PR cannot be created. Empty list means allowed."""
    reasons = []
    if execution.mode != 'apply' or execution.state not in ('succeeded', 'partial_success'):
        reasons.append('La ejecución debe ser de tipo apply y haber terminado correctamente')
    if execution.rolled_back:
        reasons.append('El candidato fue descartado (rollback)')
    for g in evaluate(db, execution):
        if g['required'] and g['status'] != 'PASS':
            reasons.append(f"Gate {g['gate']} ({g['title']}) obligatorio en estado {g['status']}")
    plan = db.get(Plan, execution.plan_id)
    open_blocking = db.scalar(select(Finding.id).where(Finding.scan_id == plan.scan_id, Finding.blocking.is_(True),
                                                       Finding.status.not_in(['resolved', 'discarded'])).limit(1))
    if open_blocking:
        reasons.append('Existen hallazgos bloqueantes sin resolver')
    project = db.get(Project, execution.project_id)
    from .models import Repository
    if db.get(Repository, project.repository_id).source_type != 'git':
        reasons.append('Ramas y PR solo están disponibles cuando el origen es un repositorio Git; descargue candidate.zip')
    elif not project.pr_config:
        reasons.append('El proyecto no tiene configuración Git para PR (proveedor, repositorio y referencia de credencial)')
    return reasons

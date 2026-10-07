"""Report set of improvements/12 rendered from the analysis (report) and the run result. Facts only: no readiness or
savings percentages; hours appear only when calibrated (effort-estimate.md)."""
from collections import Counter


def _table(rows, headers):
    esc = lambda v: str('' if v is None else v).replace('|', '\\|').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)] + ['| ' + ' | '.join(esc(c) for c in r) + ' |' for r in rows])


def executive_summary(report, result, est) -> str:
    s, ov = report['summary'], report.get('overview', {})
    blockers = [f for f in report['findings'] if f['blocking']]
    applied = [x for x in result.get('steps', []) if x['state'] == 'APPLIED']
    lines = ['# Resumen ejecutivo', '',
             f"**Estado de la migración:** {result.get('status', 'NO EJECUTADA')}" + (f" ({result['primaryReason']})" if result.get('primaryReason') else ''), '',
             f"- Origen: Java {', '.join(ov.get('java', [])) or '?'} · Camel {', '.join(ov.get('camel', [])) or '?'} · runtime {ov.get('runtime', '?')}",
             f"- Tamaño: {s['modules']} módulo(s), {s['routesXmlParsed'] + s['routesJavaApproximate']} ruta(s), "
             f"{sum(s['integrationsByType'].values())} integración(es) externas",
             f"- Hallazgos: {s['findings']} ({', '.join(f'{k}: {v}' for k, v in s['findingsBySeverity'].items())}); bloqueantes: {len(blockers)}",
             f"- Pasos automáticos aplicados: {len(applied)} · acciones manuales: {result.get('manualActions', 0)}",
             f"- Compilación del candidato: {result.get('summary', {}).get('build', 'NO EJECUTADA')} · pruebas: {result.get('summary', {}).get('tests') or 'n/a'}",
             f"- Puntaje de complejidad (relativo, no horas): {est['complexity']['score']}" if est.get('complexity') else '',
             f"- Esfuerzo: {est['knownHours']} h conocidas" + ('' if est['complete'] else ' — incompleto: categorías sin calibrar (ver effort-estimate.md)'),
             '', '## Riesgos principales', '']
    top = Counter(f['ruleId'] for f in report['findings'] if f['severity'] == 'high' or f['blocking'])
    lines += [f'- {r}: {n} ocurrencia(s)' for r, n in top.most_common(8)] or ['- Ninguno de severidad alta.']
    lines += ['', 'Compilar y pasar pruebas no demuestra equivalencia funcional: ver integration-inventory.md y manual-actions.md.']
    return '\n'.join(x for x in lines if x is not None) + '\n'


def technical_analysis(report) -> str:
    mods = report['modules']
    lines = ['# Análisis técnico', '', f"Catálogo de reglas {report['catalogVersion']} · resolución Maven: {report.get('mavenResolution')}", '',
             '## Módulos', '', _table([(m['file'], m['artifactId'], m['packaging'], m.get('javaResolved') or m.get('javaObserved'),
                                       ', '.join(m.get('camelVersions', []))) for m in mods], ['POM', 'Artefacto', 'Packaging', 'Java', 'Camel']),
             '', '## Hallazgos por regla', '']
    by = {}
    for f in report['findings']:
        by.setdefault(f['ruleId'], []).append(f)
    lines.append(_table([(r, fs[0]['severity'], fs[0]['classification'], fs[0].get('migrationAction', ''), len(fs), fs[0]['recommendation'][:140])
                         for r, fs in sorted(by.items())], ['Regla', 'Severidad', 'Clasificación', 'Acción', 'N', 'Recomendación']))
    g = report.get('graph') or {}
    lines += ['', '## Grafo de integración', '',
              f"{sum(n['kind'] == 'route' for n in g.get('nodes', []))} rutas, {sum(n['kind'] == 'endpoint' for n in g.get('nodes', []))} endpoints, "
              f"{sum(e['kind'] == 'channel' for e in g.get('edges', []))} enlaces entre rutas (direct/vm/seda/JMS)."
              + (' Las rutas Java DSL se detectan por expresión regular (aproximado).' if g.get('approximate') else ''),
              '', 'Detalle en analysis.json (`graph.nodes`, `graph.edges`).']
    return '\n'.join(lines) + '\n'


def integration_inventory(report) -> str:
    rows = [(i['type'], i['component'], i['direction'], i['uri'] or '(dinámico)', i['routeId'] or '', f"{i['file']}:{i['line']}")
            for i in sorted(report.get('integrations', []), key=lambda i: (i['type'], i['file'], i['line']))]
    lines = ['# Inventario de integraciones', '', _table(rows, ['Tipo', 'Componente', 'Dirección', 'URI (redactada)', 'Ruta', 'Ubicación']) if rows else 'Ninguna.',
             '', 'Cada integración requiere evidencia de equivalencia (docs/16_EQUIVALENCIA.md): contrato, errores, reintentos y transacciones.']
    return '\n'.join(lines) + '\n'


def security_report(report, result, sbom_count=None) -> str:
    sec = result.get('security') or {}
    secrets = [f for f in report['findings'] if f['ruleId'] == 'HARDCODED_SECRET']
    excluded = [e['file'] for e in report.get('errors', []) if e['code'] == 'SENSITIVE_FILE_EXCLUDED']
    lines = ['# Reporte de seguridad', '',
             f"- Secret scan del candidato: **{sec.get('status', 'NOT_RUN')}**" + (f" ({sec['reason']})" if sec.get('reason') else ''),
             f"- Secretos detectados en el origen: {len(secrets)} · externalizados: {sec.get('secretsExternalized', 'n/a')}",
             f"- Archivos sensibles excluidos del candidato: {len(excluded)}",
             f"- SBOM: reports/sbom.cdx.json ({sbom_count} componentes)" if sbom_count is not None else '- SBOM: no generado',
             '- Escaneo de vulnerabilidades de dependencias: NOT_RUN (requiere una base de vulnerabilidades aprobada por la organización; '
             'la herramienta no envía el inventario a servicios externos)', '',
             'Estados: PASS (sin hallazgos), FINDINGS (hallazgos abiertos), ERROR (fallo de la herramienta; nunca se reporta como hallazgo).', '']
    if secrets:
        lines += ['## Secretos en el origen (valores nunca mostrados)', '', _table([(f['file'], f['line']) for f in secrets], ['Archivo', 'Línea'])]
    if sec.get('remainingFiles'):
        lines += ['', '## Pendientes en el candidato', ''] + [f'- {f}' for f in sec['remainingFiles']]
    return '\n'.join(lines) + '\n'


def build_report(result) -> str:
    rows = [(v.get('stage', ''), v['name'], v.get('round', ''), v['status'], v.get('reason') or '',
             len(v.get('errors') or [])) for v in result.get('validations', [])]
    lines = ['# Reporte de build', '', _table(rows, ['Etapa', 'Validación', 'Ronda', 'Estado', 'Motivo', 'Errores']) if rows else 'Sin validaciones.', '']
    errs = [e for v in result.get('validations', []) for e in v.get('errors') or []]
    if errs:
        lines += ['## Errores clasificados', '', _table(list(dict.fromkeys((e.get('stage', ''), e['category'], e.get('origin', ''), e['message'][:160])
                                                                             for e in errs)), ['Etapa', 'Categoría', 'Origen', 'Error'])]
    if result.get('autofix'):
        lines += ['', '## Autocorrección', '', _table([(a['round'], a['fix']['trigger'], a['applied']) for a in result['autofix']], ['Ronda', 'Disparador', 'Aplicado'])]
    return '\n'.join(lines) + '\n'


def write_all(reports_dir, report, result, est, sbom_count=None):
    for name, text in (('executive-summary.md', executive_summary(report, result, est)), ('technical-analysis.md', technical_analysis(report)),
                       ('integration-inventory.md', integration_inventory(report)), ('security-report.md', security_report(report, result, sbom_count)),
                       ('build-report.md', build_report(result))):
        (reports_dir / name).write_text(text)

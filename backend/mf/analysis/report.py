"""HTML rendering of a scan report. Every value is escaped."""
import html
import json


def render(report):
    esc = lambda x: html.escape('' if x is None else str(x))
    rows = ''.join('<tr>' + ''.join(f'<td>{esc(f.get(k))}</td>' for k in ('file', 'line', 'ruleId', 'severity', 'classification', 'recommendation')) + '</tr>'
                   for f in report['findings'])
    mods = ''.join(f"<tr><td>{esc(m['file'])}</td><td>{esc(m['artifactId'])}</td><td>{esc(m['packaging'])}</td>"
                   f"<td>{esc(m.get('javaObserved'))} / {esc(m.get('javaResolved') or 'no resuelto')}</td>"
                   f"<td>{esc(', '.join(m.get('camelVersions', [])))}</td><td>{esc(m['resolution'])}</td></tr>"
                   for m in report.get('modules', []))
    return ('<!doctype html><html lang="es"><meta charset="utf-8"><title>Inventario Camel</title>'
            '<style>body{font:16px system-ui;margin:40px;color:#153046}table{border-collapse:collapse;width:100%}'
            'td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left}pre{white-space:pre-wrap}</style>'
            '<h1>Inventario estático Camel</h1><p>Rutas Java detectadas por expresión regular (aproximadas); rutas XML parseadas. '
            'No acredita compatibilidad ni migración completa.</p><pre>' + esc(json.dumps(report.get('summary', {}), indent=2)) + '</pre>'
            '<h2>Módulos</h2><table><tr><th>POM</th><th>Artefacto</th><th>Empaquetado</th><th>Java observado / resuelto</th>'
            '<th>Camel</th><th>Resolución</th></tr>' + mods + '</table>'
            '<h2>Hallazgos</h2><table><tr><th>Archivo</th><th>Línea</th><th>Regla</th><th>Severidad</th><th>Clase</th><th>Recomendación</th></tr>'
            + rows + '</table><h2>Errores de análisis</h2><pre>' + esc(json.dumps(report.get('errors', []), indent=2)) + '</pre></html>')

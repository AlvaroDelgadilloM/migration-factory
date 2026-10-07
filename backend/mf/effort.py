"""Effort estimate = work units measured by the analysis × hours per unit defined or calibrated by the organization.

No hours are invented: a category without configured hours and without calibration data shows its units only and is
listed as "sin calibrar". Calibration = median minutes per unit from time actually recorded during reviews.
"""
import json
import statistics
from pathlib import Path

from .config import REPO_ROOT

CONFIG_PATH = REPO_ROOT / 'rules' / 'effort.json'


def load_config(path=None) -> dict:
    return json.loads(Path(path or CONFIG_PATH).read_text(encoding='utf-8'))


def category_of(rule_id: str, config: dict) -> str | None:
    return next((k for k, c in config['categories'].items() if rule_id in c['rules']), None)


def work_units(findings, integrations, route_builders=(), test_classes=0, changed_files=None, config=None) -> dict:
    """{category: {'units': n, 'detail': [...]}} grouped by the category's unit (class/file/occurrence/module)."""
    config = config or load_config()
    out = {}
    for key, c in config['categories'].items():
        hits = [f for f in findings if f['ruleId'] in c['rules']]
        if not hits:
            continue
        if key == 'test-coverage':
            units = max(0, len(route_builders) - test_classes)
            detail = [b['class'] for b in route_builders][:20]
        elif c['unit'] in ('clase', 'archivo', 'módulo'):
            detail = sorted({f['file'] for f in hits})
            units = len(detail)
        else:
            detail = sorted({f"{f['file']}:{f['line']}" for f in hits})
            units = len(detail)
        if units:
            out[key] = {'units': units, 'findings': len(hits), 'detail': detail[:20]}
    eq = {}
    for i in integrations:
        if i['type'] == 'INTER_CONTEXT':
            continue
        key = f"{i['type']}|{i['component']}|{i.get('uri') or i['file']}"  # one integration = one distinct endpoint
        eq.setdefault(i['type'], set()).add(key)
    for t, keys in eq.items():
        out[f'equivalence:{t}'] = {'units': len(keys), 'findings': 0, 'detail': sorted(keys)[:20]}
    if changed_files is not None:
        out['diffReview'] = {'units': changed_files, 'findings': 0, 'detail': []}
    out['fixed'] = {'units': 1, 'findings': 0, 'detail': []}
    return out


def calibrate(logs) -> dict:
    """logs: [{'category', 'minutes', 'units'}] -> {category: {'hours': median hours per unit, 'samples': n}}."""
    per = {}
    for l in logs:
        if l['minutes'] and l.get('units', 1):
            per.setdefault(l['category'], []).append(l['minutes'] / 60 / max(1, l.get('units', 1)))
    return {k: {'hours': round(statistics.median(v), 2), 'samples': len(v)} for k, v in per.items()}


def estimate(units: dict, config=None, calibration=None) -> dict:
    config, calibration = config or load_config(), calibration or {}
    rows, total, uncalibrated = [], 0.0, []
    for key, u in units.items():
        if key.startswith('equivalence:'):
            t = key.split(':', 1)[1]
            label, unit = f"{config['equivalence']['label']} — {t}", config['equivalence']['unit']
            configured = config['equivalence']['hoursByType'].get(t)
        elif key in ('diffReview', 'fixed'):
            label, unit, configured = config[key]['label'], config[key]['unit'], config[key]['hours']
        else:
            c = config['categories'][key]
            label, unit, configured = c['label'], c['unit'], c['hours']
        cal = calibration.get(key)
        hours, source = (configured, 'configurado') if configured is not None else ((cal['hours'], f"calibrado ({cal['samples']} mediciones)") if cal else (None, 'sin calibrar'))
        subtotal = round(u['units'] * hours, 2) if hours is not None else None
        if subtotal is None:
            uncalibrated.append(key)
        else:
            total += subtotal
        rows.append({'category': key, 'label': label, 'unit': unit, 'units': u['units'], 'findings': u['findings'],
                     'hoursPerUnit': hours, 'source': source, 'subtotalHours': subtotal, 'detail': u['detail']})
    return {'configVersion': config['version'], 'rows': rows, 'knownHours': round(total, 2), 'uncalibrated': uncalibrated,
            'complete': not uncalibrated,
            'note': 'Estimación = unidades medidas × horas por unidad definidas o calibradas por su organización. '
                    'Las categorías sin calibrar no suman horas. No incluye puesta en producción ni coordinación con consumidores.'}


PROPRIETARY = ('JBOSS', 'JBOSS_DEPLOYMENT', 'JBOSS_SECURITY', 'EJB', 'JTA')


def complexity_score(report) -> dict:
    """improvements/12 formula, weights as given there. A relative score to compare projects, never hours."""
    integ = report.get('integrations', [])
    parts = {'routes': len(report.get('routes', [])), 'soap': sum(i['type'] in ('SOAP', 'REST_CXF') for i in integ),
             'jms': sum(i['type'] == 'JMS' for i in integ),
             'proprietaryApis': sum(f['ruleId'] in PROPRIETARY for f in report.get('findings', [])),
             'modules': len(report.get('modules', []))}
    weights = {'routes': 1, 'soap': 3, 'jms': 2, 'proprietaryApis': 5, 'modules': 2}
    return {'score': sum(parts[k] * weights[k] for k in parts), 'factors': parts, 'weights': weights,
            'formula': 'routes*1 + soap*3 + jms*2 + proprietaryApis*5 + modules*2',
            'note': 'Puntaje relativo para comparar proyectos; no son horas (requiere calibración histórica).'}


def markdown(est: dict) -> str:
    esc = lambda v: str('' if v is None else v).replace('|', '\\|')
    lines = ['# Estimación de esfuerzo', '', est['note'], '',
             '| Categoría | Unidad | Unidades | Horas/unidad | Fuente | Subtotal (h) |', '|---|---|---|---|---|---|']
    for r in est['rows']:
        lines.append(f"| {esc(r['label'])} | {esc(r['unit'])} | {r['units']} | {esc(r['hoursPerUnit'] if r['hoursPerUnit'] is not None else '—')} "
                     f"| {esc(r['source'])} | {esc(r['subtotalHours'] if r['subtotalHours'] is not None else '—')} |")
    lines += ['', f"**Horas conocidas:** {est['knownHours']}" + ('' if est['complete'] else
              f" (incompleto: {len(est['uncalibrated'])} categoría(s) sin calibrar)"), '',
              'Para completar: defina horas en `rules/effort.json` o registre el tiempo real al revisar (calibración por mediana).']
    if est.get('complexity'):
        c = est['complexity']
        lines += ['', '## Puntaje de complejidad', '', f"`{c['formula']}` = **{c['score']}**", '',
                  ', '.join(f'{k}: {v}' for k, v in c['factors'].items()), '', c['note']]
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':  # self-check
    cfg = load_config()
    findings = [{'ruleId': 'CDI_SCOPE', 'file': 'A.java', 'line': 1}, {'ruleId': 'CDI_SCOPE', 'file': 'A.java', 'line': 9},
                {'ruleId': 'CDI_SCOPE', 'file': 'B.java', 'line': 3}, {'ruleId': 'HARDCODED_SECRET', 'file': 'p.properties', 'line': 2}]
    integ = [{'type': 'JMS', 'component': 'jms', 'uri': 'jms:queue:A', 'file': 'R.java'}, {'type': 'JMS', 'component': 'jms', 'uri': 'jms:queue:A', 'file': 'R.java'},
             {'type': 'JMS', 'component': 'jms', 'uri': 'jms:queue:B', 'file': 'R.java'}]
    u = work_units(findings, integ, changed_files=4, config=cfg)
    assert u['cdi-bean']['units'] == 2 and u['secrets']['units'] == 1 and u['equivalence:JMS']['units'] == 2
    est = estimate(u, cfg, calibrate([{'category': 'cdi-bean', 'minutes': 30, 'units': 1}, {'category': 'cdi-bean', 'minutes': 90, 'units': 1},
                                      {'category': 'cdi-bean', 'minutes': 60, 'units': 1}]))
    row = next(r for r in est['rows'] if r['category'] == 'cdi-bean')
    assert row['hoursPerUnit'] == 1.0 and row['subtotalHours'] == 2.0 and 'calibrado (3' in row['source']
    assert not est['complete'] and est['knownHours'] == 2.0  # uncalibrated categories never add invented hours
    c = complexity_score({'routes': [1, 2], 'integrations': [{'type': 'SOAP'}, {'type': 'JMS'}], 'findings': [{'ruleId': 'EJB'}], 'modules': [1]})
    assert c['score'] == 2 + 3 + 2 + 5 + 2
    print('ok')

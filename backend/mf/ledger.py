"""Change ledger (improvements/04, 05, 14): one entry per applied step with rule, file, lines, before, after,
confidence level, rationale and an individual rollback patch. Built from the per-step commits of the candidate."""
import hashlib
import re

from . import rules as mrules
from .security import redact

HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
MAX_LINES = 20


def parse_patch(patch: str) -> list[dict]:
    """Unified diff -> [{file, hunks: [{oldStart, oldLines, newStart, newLines, before, after}]}]."""
    files, cur, hunk = [], None, None
    for line in patch.splitlines():
        if line.startswith('diff --git '):
            cur = {'file': line.split(' b/', 1)[-1], 'hunks': []}
            files.append(cur)
            hunk = None
        elif cur is not None and line.startswith('+++ ') and line[4:] != '/dev/null':
            cur['file'] = line[6:] if line.startswith('+++ b/') else line[4:]
        elif cur is not None and (m := HUNK.match(line)):
            hunk = {'oldStart': int(m[1]), 'oldLines': int(m[2] or 1), 'newStart': int(m[3]), 'newLines': int(m[4] or 1), 'before': [], 'after': []}
            cur['hunks'].append(hunk)
        elif hunk is not None and line[:1] in '-+' and not line.startswith(('---', '+++')):
            hunk['before' if line[0] == '-' else 'after'].append(line[1:])
    return files


def _excerpt(lines):
    text = '\n'.join(lines[:MAX_LINES]) + (f'\n… ({len(lines) - MAX_LINES} líneas más)' if len(lines) > MAX_LINES else '')
    return redact(text)


def project_factors(report) -> list[str]:
    """Factors from improvements/05 that apply to the whole project; stated as facts, never as a number."""
    by_rule = {}
    for f in report.get('findings', []):
        by_rule[f['ruleId']] = by_rule.get(f['ruleId'], 0) + 1
    tests = report.get('testClasses', report.get('summary', {}).get('testClasses', 0))
    out = [f'{tests} clase(s) de prueba en el proyecto' if tests else 'Sin pruebas en el proyecto: el comportamiento no se verifica automáticamente']
    prop = sum(by_rule.get(r, 0) for r in ('JBOSS', 'JBOSS_DEPLOYMENT', 'JBOSS_SECURITY', 'EJB', 'JTA'))
    if prop:
        out.append(f'{prop} uso(s) de APIs propietarias JBoss/EJB/JTA')
    dyn = report.get('summary', {}).get('dynamicEndpoints', 0)
    if dyn:
        out.append(f'{dyn} endpoint(s) dinámicos (toD / variables): no se resuelven estáticamente')
    if by_rule.get('MAVEN_UNRESOLVED'):
        out.append('Configuración Maven incompleta (versiones sin resolver)')
    return out


def build(steps_result, plan, patches: dict, factors: list[str], snapshots: dict) -> list[dict]:
    """steps_result: CLI/worker step results; patches: key -> patch of that step; snapshots: key -> (before, after) commit."""
    plan_by_key = {s['key']: s for s in plan['steps']}
    entries = []
    for n, r in enumerate([r for r in steps_result if r.get('state') == 'APPLIED' and r['key'] in patches], 1):
        ps = plan_by_key.get(r['key'], {})
        recipe = ps.get('recipe') or {}
        level = ps.get('classification', 'REVIEW')
        rule_ids = list(dict.fromkeys(list(recipe.get('rules', [])) + [e['rule'] for e in recipe.get('edits', []) if e.get('rule')]
                                      + [f['ruleId'] for f in ps.get('findings', [])]))
        patch = patches[r['key']]
        name = f"steps/{n:02d}-{r['key']}.patch"
        changes = [{'file': f['file'], 'lines': f"{h['newStart']}-{h['newStart'] + max(h['newLines'], 1) - 1}",
                    'before': _excerpt(h['before']), 'after': _excerpt(h['after'])} for f in parse_patch(patch) for h in f['hunks']]
        mech = (['Receta OpenRewrite (transformación sobre el AST) con versiones fijadas: ' + ', '.join(recipe.get('activeRecipes', []))]
                if recipe.get('activeRecipes') else ['Edición localizada: archivo, línea y literal exactos encontrados por el análisis'])
        before, after = snapshots.get(r['key'], (None, None))
        entries.append({'step': r['key'], 'kind': ps.get('kind'), 'rules': rule_ids, 'confidence': level,
                        'confidenceMeaning': mrules.load()['levels'].get(level, ''), 'rationale': mech + factors,
                        'snapshotBefore': before, 'snapshotAfter': after, 'files': sorted({c['file'] for c in changes}),
                        'patch': name, 'patchSha256': hashlib.sha256(patch.encode()).hexdigest(),
                        'rollback': {'available': True, 'command': f'git apply -R reports/{name}',
                                     'note': 'Reversible de forma individual si ningún paso posterior tocó las mismas líneas; si no, revertir en orden inverso.'},
                        'changes': changes})
    return entries


def markdown(entries) -> str:
    out = ['# Registro de cambios', '', 'Una entrada por paso aplicado. La confianza es un nivel (AUTO, AUTO_TEST, REVIEW, MANUAL), no un porcentaje.', '']
    if not entries:
        return '\n'.join(out + ['Ningún cambio aplicado.']) + '\n'
    for e in entries:
        out += [f"## {e['step']} — {e['confidence']}", '', f"- Significado: {e['confidenceMeaning']}",
                f"- Reglas: {', '.join(e['rules']) or '—'}", f"- Snapshot: {(e['snapshotBefore'] or '')[:12]} → {(e['snapshotAfter'] or '')[:12]}",
                f"- Rollback: `{e['rollback']['command']}` (sha256 {e['patchSha256'][:16]})", '- Fundamento:'] + [f'  - {x}' for x in e['rationale']]
        out.append('')
        for c in e['changes'][:50]:
            out += [f"### {c['file']} (líneas {c['lines']})", '', '```diff'] + [f'- {x}' for x in c['before'].splitlines()] \
                + [f'+ {x}' for x in c['after'].splitlines()] + ['```', '']
        if len(e['changes']) > 50:
            out.append(f"… {len(e['changes']) - 50} bloques más en reports/{e['patch']}\n")
    return '\n'.join(out) + '\n'


if __name__ == '__main__':  # self-check
    p = ('diff --git a/pom.xml b/pom.xml\n--- a/pom.xml\n+++ b/pom.xml\n@@ -10,2 +10,2 @@\n'
         '-  <artifactId>camel-http4</artifactId>\n+  <artifactId>camel-http</artifactId>\n'
         'diff --git a/a.properties b/a.properties\n--- a/a.properties\n+++ b/a.properties\n@@ -3 +3 @@\n-db.password=S3cr3t\n+db.password=${MF_SECRET_DB_PASSWORD}\n')
    files = parse_patch(p)
    assert [f['file'] for f in files] == ['pom.xml', 'a.properties'] and files[1]['hunks'][0]['newStart'] == 3
    plan = {'steps': [{'key': 'camel-3-to-4', 'kind': 'recipe', 'classification': 'AUTO_TEST',
                       'recipe': {'activeRecipes': ['x.Y'], 'rules': ['CAMEL_HTTP4_TO_HTTP']}, 'findings': [{'ruleId': 'HTTP4'}]}]}
    e = build([{'key': 'camel-3-to-4', 'state': 'APPLIED'}, {'key': 'z', 'state': 'ROLLED_BACK'}], plan, {'camel-3-to-4': p},
              project_factors({'findings': [{'ruleId': 'JBOSS'}], 'testClasses': 0}), {'camel-3-to-4': ('a' * 40, 'b' * 40)})
    assert len(e) == 1 and e[0]['rules'] == ['CAMEL_HTTP4_TO_HTTP', 'HTTP4'] and e[0]['confidence'] == 'AUTO_TEST'
    assert e[0]['changes'][0]['before'] == '  <artifactId>camel-http4</artifactId>' and e[0]['changes'][1]['lines'] == '3-3'
    assert 'S3cr3t' not in str(e) and any('Sin pruebas' in x for x in e[0]['rationale'])
    assert '## camel-3-to-4 — AUTO_TEST' in markdown(e)
    print('ok')

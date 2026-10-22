"""Doc 58 fallback: an LLM PROPOSES an Oracle SQL expression for each Transformer expression the deterministic translator
refused. Every proposal goes through deterministic validators (no statement, no comment, balanced, only known columns); a
valid one is still never applied: it is written to ai-proposals.json/.md and to the worklist as a review item.

Off unless MF_AI_ENABLED=true. The API key is a credential reference (MF_AI_CREDENTIAL_REF, default env:MF_CRED_AI): the
worker captures it at start and removes it from its environment like every MF_CRED_* (Maven never sees it). What is sent: the
expression, its column name and the input column names; never data, parameter values or connection properties."""
from __future__ import annotations

import json
import os
import re

from .expressions import Untranslatable, stage_vars_sql, translate
from .registry import param_resolver

API_URL = 'https://api.anthropic.com/v1/messages'
DEFAULT_MODEL = 'claude-sonnet-5-5'
FORBIDDEN = re.compile(r'\b(SELECT|INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE|BEGIN|DECLARE|CALL|COMMIT|'
                       r'ROLLBACK|DBMS_\w+|UTL_\w+)\b', re.I)
SYSTEM = ('You translate one IBM DataStage Transformer derivation to ONE Oracle SQL expression (Oracle 19c). Input columns are '
          'referenced as src.<COLUMN> and stage variables as src.MF_SV_<NAME>; job parameters as the literal {{ params.<NAME> }}. '
          'Answer only JSON: {"sql": "<expression>", "notes": "<one sentence, Spanish>"}; if it cannot be expressed as a single '
          'expression, answer {"sql": null, "notes": "<why, Spanish>"}. Never a statement, subquery, comment or semicolon.')


def enabled() -> bool:
    return os.environ.get('MF_AI_ENABLED', '').lower() == 'true'


def credential_ref() -> str:
    return os.environ.get('MF_AI_CREDENTIAL_REF', 'env:MF_CRED_AI')


def candidates(project) -> list[dict]:
    """Every Transformer derivation/constraint the deterministic translator refuses, with the context the model may see."""
    out = []
    for job in project.jobs:
        params = param_resolver(job)
        for stage in (s for s in job.stages if s.stage_type == 'CTransformerStage'):
            ins = [l for l in job.links if l.target_stage == stage.name]
            inputs = sorted({c['name'] for l in ins for c in l.columns if c.get('name')})
            cols = {'*': 'src'}
            try:
                cols |= {n: f'src.MF_SV_{n}' for n in stage_vars_sql(stage.stage_vars, params, 'MF_ORDER')[0]}
            except Untranslatable:
                cols |= {v['name']: f'src.MF_SV_{v["name"]}' for v in stage.stage_vars}
            svs = sorted(f'MF_SV_{v["name"]}' for v in stage.stage_vars)
            for link in (l for l in job.links if l.source_stage == stage.name):
                items = list(link.expressions.items()) + ([('(restricción)', link.constraint)] if link.constraint else [])
                for col, expr in items:
                    try:
                        translate(expr, cols, params)
                    except Untranslatable as e:
                        out.append({'job': job.name, 'stage': stage.name, 'link': link.name, 'column': col, 'expression': expr,
                                    'error': str(e), 'inputs': inputs, 'stageVars': svs})
    return out


def validate(sql, item) -> list[str]:
    """Deterministic gate for a proposal: [] = acceptable to show for review."""
    if not isinstance(sql, str) or not sql.strip():
        return ['sin expresión']
    problems = []
    bare = re.sub(r"'(?:[^']|'')*'", "''", sql)  # string literals are data: the checks below look at the code around them
    if len(sql) > 2000:
        problems.append('demasiado larga')
    if ';' in bare or '--' in bare or '/*' in bare:
        problems.append('contiene ; o comentarios')
    if m := FORBIDDEN.search(bare):
        problems.append(f'usa {m[0].upper()} (solo se acepta una expresión)')
    if bare.count("'") % 2:
        problems.append('comillas sin cerrar')
    depth = 0
    for ch in bare:
        depth += (ch == '(') - (ch == ')')
        if depth < 0:
            break
    if depth:
        problems.append('paréntesis desbalanceados')
    known = set(item['inputs']) | set(item['stageVars'])
    unknown = sorted({c for c in re.findall(r'\bsrc\.(\w+)', bare, re.I) if c not in known})
    if unknown:
        problems.append('columnas inexistentes: ' + ', '.join(unknown[:5]))
    return problems


def anthropic_call(prompt: str, key: str) -> str:
    import httpx
    r = httpx.post(API_URL, timeout=60, headers={'x-api-key': key, 'anthropic-version': '2023-06-01',
                                                 'content-type': 'application/json'},
                   json={'model': os.environ.get('MF_AI_MODEL', DEFAULT_MODEL), 'max_tokens': 600, 'system': SYSTEM,
                         'messages': [{'role': 'user', 'content': prompt}]})
    r.raise_for_status()
    return ''.join(b.get('text', '') for b in r.json().get('content', []))


def propose(project, call, limit: int | None = None, log=lambda m: None) -> list[dict]:
    """call(prompt) -> model text. One proposal per refused expression (at most `limit`, MF_AI_MAX_ITEMS, default 50).
    A failed call is recorded, not raised."""
    limit = limit if limit is not None else int(os.environ.get('MF_AI_MAX_ITEMS', '50'))
    items = candidates(project)
    out = []
    for i, item in enumerate(items[:limit]):
        prompt = json.dumps({'derivation': item['expression'], 'targetColumn': item['column'], 'inputColumns': item['inputs'],
                             'stageVariables': item['stageVars'], 'whyRefused': item['error']}, ensure_ascii=False)
        rec = {k: item[k] for k in ('job', 'stage', 'link', 'column', 'expression', 'error')}
        try:
            text = call(prompt)
            m = re.search(r'\{.*\}', text, re.S)
            ans = json.loads(m[0]) if m else {}
            sql, notes = ans.get('sql'), str(ans.get('notes') or '')[:500]
        except Exception as e:  # noqa: BLE001 — the proposal is optional; the run never fails because of it
            out.append(rec | {'status': 'AI_ERROR', 'problems': [type(e).__name__]})
            continue
        if sql is None:
            out.append(rec | {'status': 'AI_DECLINED', 'notes': notes, 'problems': []})
            continue
        problems = validate(sql, item)
        out.append(rec | {'status': 'AI_PROPOSED_REJECTED' if problems else 'AI_PROPOSED_VALID', 'sql': str(sql)[:2000], 'notes': notes,
                          'problems': problems})
        log(f'IA {i + 1}/{min(len(items), limit)}: {item["job"]}/{item["stage"]}.{item["column"]} → {out[-1]["status"]}')
    if len(items) > limit:
        out.append({'status': 'AI_SKIPPED', 'problems': [f'{len(items) - limit} expresión(es) más sin propuesta (límite {limit})']})
    return out


def markdown(proposals: list[dict]) -> str:
    lines = ['# Propuestas de IA para expresiones sin traducción determinística', '',
             'Generadas por un modelo de lenguaje y revisadas por validadores determinísticos (sin sentencias, sin comentarios, '
             'paréntesis y comillas balanceados, solo columnas de entrada conocidas). **Ninguna se aplicó al SQL generado**: '
             'cópiela al SQL de la etapa solo después de revisarla y reconciliar los datos.', '']
    for p in proposals:
        if p['status'] == 'AI_SKIPPED':
            lines.append(f"- {p['problems'][0]}")
            continue
        lines += [f"## {p['job']} / {p['stage']} / {p['link']}.{p['column']} — {p['status']}", '', f"DataStage: `{p['expression']}`", '']
        if p.get('sql'):
            lines += ['```sql', p['sql'], '```', '']
        if p.get('notes'):
            lines.append(f"Nota del modelo: {p['notes']}")
        if p.get('problems'):
            lines.append('Rechazada por: ' + '; '.join(p['problems']))
        lines.append('')
    return '\n'.join(lines)

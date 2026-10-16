"""Pruebas on the platform: one automatic run (the same cases to the original and to the migrated system, the comparison
and the evidence report) and one manual request. The worker and the API call these functions; they know nothing about the
database, so they run the same from the CLI, the worker or a test."""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from . import evidence, verify

UI_BODY_LIMIT = 20000  # characters per side kept for the screen; the full answers are in the responses.json artifact
MASKED = ('authorization', 'cookie', 'proxy-authorization')


def mask(headers: dict | None) -> dict:
    return {k: ('***' if k.lower() in MASKED else v) for k, v in (headers or {}).items()}


def _side(r: dict) -> dict:
    body = r.get('body') or ''
    return {'status': r['status'], 'type': r.get('type', ''), 'body': body[:UI_BODY_LIMIT], 'truncated': len(body) > UI_BODY_LIMIT}


def run(original: dict, migrated: dict, cases: list, out: Path, screenshots: bool = True, timeout: int = 60, log=lambda m: None) -> dict:
    """original/migrated: {label, url}. Writes out/reports (responses, evidence) and out/evidence.zip; returns the summary."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    cases_file = out / 'cases.json'
    cases_file.write_text(json.dumps({'cases': cases}, ensure_ascii=False, indent=2), encoding='utf-8')
    res = verify.responses(original['url'], migrated['url'], cases_file, out, timeout, log)
    rep = evidence.report(out, screenshots=screenshots, labels=(original['label'], migrated['label']), log=log)
    target = Path(rep['report']).parent
    with zipfile.ZipFile(out / 'evidence.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for f in sorted(target.rglob('*')):
            if f.is_file():
                z.write(f, Path('evidencia') / f.relative_to(target))
    shutil.copy(out / 'reports' / 'responses.json', out / 'responses.json')
    return {
        'status': res['status'], 'totals': res['totals'], 'differencesByKind': res.get('differencesByKind', {}),
        'screenshots': rep['screenshots'], 'screenshotProblem': rep['problem'],
        'cases': [{'name': c['name'], 'method': c['method'], 'path': c['path'], 'state': c['state'], 'kind': c.get('kind'),
                   'issues': c['issues'], 'accept': c.get('accept'), 'request': {**c['request'], 'headers': mask(c['request'].get('headers'))},
                   'original': _side(c['original']), 'migrated': _side(c['migrated'])} for c in res['cases']],
    }


def manual(request: dict, targets: list, timeout: int = 30) -> dict:
    """One request (REST, XML or SOAP) to one or two systems. With two, the answers are compared exactly as an automatic run
    does; nothing is stored."""
    # with two systems their own addresses are neutralized (as in an automatic run) so a quoted URL is not a difference
    hosts = tuple(sorted({t['url'].split('//', 1)[-1].rstrip('/') for t in targets}, key=len, reverse=True)) if len(targets) == 2 else ()
    answers = [verify._send(t['url'], request, timeout, hosts) for t in targets]
    result = {'results': [{'label': t['label'], 'url': t['url'], **_side(a)} for t, a in zip(targets, answers)]}
    if len(answers) == 2:
        state, issues, kind = verify.compare_pair(*answers)
        result |= {'state': state, 'issues': issues, 'kind': kind}
    return result

"""Dependency vulnerability scan of the candidate's SBOM with osv-scanner (pinned in the worker image).

Offline matching: the scanner downloads the public OSV database for Maven into the job workspace and compares locally, so the
dependency inventory is never sent to any service (the reason the scan used to be NOT_RUN). States: PASS (no known
vulnerability), FINDINGS (open vulnerabilities, listed), ERROR (the tool failed; never reported as findings), NOT_RUN (no tool).
The database is per job (no shared cache a build could tamper with)."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

OSV_VERSION = '2.7.0'


def scanner() -> str | None:
    return os.environ.get('MF_OSV_SCANNER') or shutil.which('osv-scanner')


def _severity(v: dict) -> str:
    s = (v.get('database_specific') or {}).get('severity') or ''
    return s.upper() if s else 'UNKNOWN'


def parse(data: dict) -> list[dict]:
    """osv-scanner JSON -> one row per package and vulnerability (aliases such as CVE ids kept)."""
    rows = []
    for res in data.get('results') or []:
        for p in res.get('packages') or []:
            pkg = p.get('package') or {}
            for v in p.get('vulnerabilities') or []:
                rows.append({'package': pkg.get('name'), 'version': pkg.get('version'), 'id': v.get('id'),
                             'aliases': [a for a in v.get('aliases') or [] if a.startswith('CVE-')][:5],
                             'severity': _severity(v), 'summary': (v.get('summary') or '')[:300]})
    order = {'CRITICAL': 0, 'HIGH': 1, 'MODERATE': 2, 'MEDIUM': 2, 'LOW': 3}
    return sorted(rows, key=lambda r: (order.get(r['severity'], 4), r['package'] or '', r['id'] or ''))


def scan(ctx, bom: Path, run=None) -> dict:
    """run(args, cwd, ctx, name=, timeout=, env=) defaults to the worker runner (process group, timeout, cancellation, log)."""
    exe = scanner()
    if not exe:
        return {'status': 'NOT_RUN', 'reason': 'osv-scanner no está instalado en este entorno', 'vulnerabilities': []}
    if run is None:
        from .worker.runner import run
    out = ctx.workdir / 'osv-result.json'
    res = run([exe, 'scan', 'source', '--offline-vulnerabilities', '--download-offline-databases', '--format', 'json',
               '--output-file', str(out), '-L', str(bom)], ctx.workdir, ctx, name='vulnerability-scan', timeout=600,
              env={'OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY': str(ctx.workdir / 'osv-db')})
    if res.exit_code not in (0, 1) or not out.exists():  # 0 = clean, 1 = vulnerabilities found, others = tool error
        return {'status': 'ERROR', 'reason': f'osv-scanner terminó con código {res.exit_code}', 'vulnerabilities': []}
    try:
        rows = parse(json.loads(out.read_text(encoding='utf-8')))
    except (ValueError, OSError) as e:
        return {'status': 'ERROR', 'reason': f'salida ilegible de osv-scanner: {type(e).__name__}', 'vulnerabilities': []}
    return {'status': 'FINDINGS' if rows else 'PASS', 'tool': f'osv-scanner {OSV_VERSION} (base OSV local)', 'vulnerabilities': rows,
            'packages': len({(r['package'], r['version']) for r in rows})}


if __name__ == '__main__':  # self-check of the parser
    sample = {'results': [{'packages': [{'package': {'name': 'org.apache.logging.log4j:log4j-core', 'version': '2.14.1'},
                                         'vulnerabilities': [{'id': 'GHSA-jfh8-c2jp-5v3q', 'aliases': ['CVE-2021-44228'],
                                                              'summary': 'Log4Shell', 'database_specific': {'severity': 'CRITICAL'}},
                                                             {'id': 'GHSA-x', 'aliases': []}]}]}]}
    rows = parse(sample)
    assert [r['severity'] for r in rows] == ['CRITICAL', 'UNKNOWN'] and rows[0]['aliases'] == ['CVE-2021-44228']
    print('ok')

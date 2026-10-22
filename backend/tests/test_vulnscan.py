"""Dependency vulnerability scan (osv-scanner, offline OSV database): exit codes and output mapped to PASS / FINDINGS / ERROR."""
import json

from mf import vulnscan
from mf.worker import runner


def fake_run(code, payload=None):
    def run(args, cwd, ctx, name, timeout, env):
        assert '--offline-vulnerabilities' in args and env['OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY'].startswith(str(ctx.workdir))
        if payload is not None:
            (ctx.workdir / 'osv-result.json').write_text(json.dumps(payload))
        return runner.Result(code, 0, ctx.workdir / 'x.log')
    return run


def test_scan_states(tmp_path, monkeypatch):
    ctx = runner.Context('j', tmp_path, deadline=1e12)
    monkeypatch.setenv('MF_OSV_SCANNER', '/opt/osv-scanner')
    vuln = {'results': [{'packages': [{'package': {'name': 'org.apache.logging.log4j:log4j-core', 'version': '2.14.1'},
                                       'vulnerabilities': [{'id': 'GHSA-jfh8-c2jp-5v3q', 'aliases': ['CVE-2021-44228'],
                                                            'database_specific': {'severity': 'CRITICAL'}}]}]}]}
    r = vulnscan.scan(ctx, tmp_path / 'bom.json', run=fake_run(1, vuln))
    assert r['status'] == 'FINDINGS' and r['vulnerabilities'][0]['aliases'] == ['CVE-2021-44228'] and r['packages'] == 1
    assert vulnscan.scan(ctx, tmp_path / 'bom.json', run=fake_run(0, {'results': []}))['status'] == 'PASS'
    (tmp_path / 'osv-result.json').unlink()
    assert vulnscan.scan(ctx, tmp_path / 'bom.json', run=fake_run(127))['status'] == 'ERROR'   # a tool failure is never "no findings"
    monkeypatch.setenv('MF_OSV_SCANNER', '')
    monkeypatch.setattr(vulnscan.shutil, 'which', lambda n: None)
    assert vulnscan.scan(ctx, tmp_path / 'bom.json')['status'] == 'NOT_RUN'

"""Odoo 12 -> 19 scenarios on real Odoo + OpenUpgrade (workflow odoo-e2e.yml), through the same functions the worker runs
(mf.odoo.jobs: analyze, migrate, port_code). Synthetic data only: Odoo's demo data and the synthetic modules in examples/.
    python odoo_chain.py scenario <name> <dump.sql> [--start 12.0] [--third-party N] [--oca module] [--table name=rows]
    python odoo_chain.py code <addons dir>
The database server and the seven odoo-hop-<version> runners are started by the workflow."""
import argparse
import sys
import tempfile
import time
import uuid
from pathlib import Path

from mf.odoo import chain, jobs as oj


def ctx():
    from mf.worker.runner import Context
    return Context(job_id='e2e', workdir=Path(tempfile.mkdtemp()), deadline=time.monotonic() + 5 * 3600)


def fetch(url):
    import httpx
    r = httpx.get(url, timeout=300, follow_redirects=True)
    r.raise_for_status()
    return r.content


def scenario(a):
    """Analysis -> the decisions an architect would take (uninstall what blocks, apply the detected data fixes) -> the hops ->
    reconciliation, with the checks of the scenario."""
    from mf.worker.runner import run
    c, om = ctx(), uuid.uuid4()
    res = oj.analyze(c, run, Path(a.dump), om, print)
    rows, counts = res['rows'], res['counts']
    print(f"[{a.name}] Odoo {res['sourceVersion']} · {counts} · saltos {res['hops']} · correcciones {[f['id'] for f in res['fixes']]}")
    assert res['from'] == a.start, res['from']
    third = sorted(r['name'] for r in rows if r['origin'] == 'THIRD_PARTY')
    assert len(third) == a.third_party, third
    if a.oca:
        oca = {r['name']: r for r in rows if r['origin'] == 'OCA'}
        assert a.oca in oca and oca[a.oca]['outcome'] != 'BLOCK', oca
    block = sorted(r['name'] for r in rows if r['outcome'] == 'BLOCK')
    decisions = {n: {'action': 'uninstall', 'reason': 'e2e: sin versión 19'} for n in block}
    decisions |= {f"fix:{f['id']}": {'action': 'apply', 'reason': 'e2e'} for f in res['fixes']}
    print(f"[{a.name}] desinstalar: {block}")
    m = oj.migrate(c, res, decisions, om, print, lambda i, n: None, fetch, lambda: False, 3 * 3600)
    for h in m['hops']:
        print(f"[{a.name}] {h['version']}: {h['state']} errors={h['errors']} base={h['baseVersion']}")
    if m['state'] != 'succeeded':
        print(m['hops'][-1]['logTail'][-8000:])
        sys.exit(1)
    db = m['database']
    assert chain.base_version(db).startswith('19.0'), chain.base_version(db)
    for r in m['reconciliation']:
        print(f"[{a.name}] {r['state']:15} {r['label']}: {r['before']} -> {r['after']}")
    diff = [r['check'] for r in m['reconciliation'] if r['state'] == 'DIFFERENT']
    assert not diff, diff
    installed = {r['name'] for r in chain.installed_modules(db)}
    assert not set(block) & installed, set(block) & installed
    if a.oca:
        assert a.oca in installed, f'{a.oca} no quedó instalado en 19'
    for spec in a.table or []:
        name, rows_expected = spec.split('=')
        got = chain.query(db)(f'select count(*) from {name}')
        assert got == (rows_expected,), (name, got)
        print(f"[{a.name}] tabla {name}: {got[0]} fila(s), conservada")
    chain.drop_db(db)
    chain.drop_db(res['sourceDb'])


def code(addons):
    """Custom module code: port + install check of each module on a new Odoo 19."""
    from mf.worker.runner import run
    res = oj.port_code(ctx(), run, Path(addons), uuid.uuid4(), print, lambda: False, 3 * 3600)
    print('base:', res['base']['state'], res['base']['modules'])
    for r in res['rows']:
        print(f"{r['name']}: {r['counts']} · instalar en 19: {r['install']['state']}")
        for f in r['findings']:
            if f['status'] != 'AUTO':
                print(f"    {f['status']:6} {f['pattern']} {f['file']}:{f['line']}")
        if r['install']['state'] == 'failed':
            print('\n'.join(line for line in r['install']['logTail'].splitlines() if ' ERROR ' in line or 'Error' in line)[-1500:])
    print('\n'.join(res['portWarnings'][:20]))
    state = {r['name']: r['install']['state'] for r in res['rows']}
    assert res['base']['state'] == 'succeeded', res['base']['logTail'][-3000:]
    assert state['legacy_sales_custom'] == 'succeeded' and state['legacy_account_bridge'] == 'failed', state
    assert 'tracking=True' in (res['ported'] / 'legacy_sales_custom' / 'models' / 'sale_order.py').read_text()


def main(argv):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('scenario')
    s.add_argument('name')
    s.add_argument('dump')
    s.add_argument('--start', default='12.0')
    s.add_argument('--third-party', type=int, default=0)
    s.add_argument('--oca')
    s.add_argument('--table', action='append')
    k = sub.add_parser('code')
    k.add_argument('addons')
    a = p.parse_args(argv)
    chain.ensure_control()
    scenario(a) if a.cmd == 'scenario' else code(a.addons)


if __name__ == '__main__':
    main(sys.argv[1:])

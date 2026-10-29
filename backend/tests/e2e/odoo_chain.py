"""Odoo 12 -> 19 on real Odoo + OpenUpgrade (workflow odoo-e2e.yml). Synthetic data only: Odoo's own demo data.
    python odoo_chain.py prepare | hop <version> | reconcile | code <addons dir>
The database server and the odoo-hop-<version> runners are started by the workflow; this drives them through mf.odoo.chain."""
import json
import sys

from mf.odoo import chain, fixes, modules, reconcile

SOURCE, TARGET = 'demo12', 'mig19'


def main(cmd, *args):
    if cmd == 'prepare':
        chain.ensure_control()
        chain.copy_db(SOURCE, TARGET)
        found = fixes.detect(chain.query_all(SOURCE))  # what the architect approves in the platform; the demo data has them
        print('fixes:', fixes.apply(chain.execute(TARGET), [f['id'] for f in found]))
        assert not fixes.detect(chain.query_all(TARGET))
        rows = modules.classify(chain.installed_modules(SOURCE))
        print(json.dumps(modules.summary(rows)))
        assert all(r['origin'] == 'CORE' for r in rows), [r['name'] for r in rows if r['origin'] != 'CORE']
    elif cmd == 'hop':
        version = args[0]
        task = chain.enqueue(version, TARGET)
        res = chain.wait(task, timeout=3 * 3600, poll=10)
        print(f"{version}: {res['state']} exit={res['exitCode']} errors={res['errors']} base={chain.base_version(TARGET)}")
        if res['state'] != 'succeeded':
            print(res['logTail'][-8000:])
            sys.exit(1)
    elif cmd == 'reconcile':
        rows = reconcile.run(chain.query(SOURCE), chain.query(TARGET))
        for r in rows:
            print(f"{r['state']:15} {r['label']}: {r['before']} -> {r['after']}")
        assert chain.base_version(TARGET).startswith('19.0'), chain.base_version(TARGET)
        diff = [r['check'] for r in rows if r['state'] == 'DIFFERENT']
        assert not diff, diff
        assert {r['check'] for r in rows if r['state'] == 'EQUAL'} >= {'partners', 'products', 'sale_orders', 'purchase_orders',
                                                                       'customer_invoices', 'journal_items'}

    elif cmd == 'code':  # custom module code: port + install check of each module on a new Odoo 19 (runner odoo-hop-19)
        import tempfile
        import time
        from pathlib import Path
        from mf.odoo import jobs as oj
        from mf.worker.runner import Context, run
        ctx = Context(job_id='e2e', workdir=Path(tempfile.mkdtemp()), deadline=time.monotonic() + 4 * 3600)
        res = oj.port_code(ctx, run, Path(args[0]), '00000000-0000-0000-0000-00000000c0de', print, lambda: False, 3 * 3600)
        print('base:', res['base']['state'], res['base']['modules'])
        for r in res['rows']:
            print(f"{r['name']}: {r['counts']} · instalar en 19: {r['install']['state']}")
            for f in r['findings']:
                if f['status'] != 'AUTO':
                    print(f"    {f['status']:6} {f['pattern']} {f['file']}:{f['line']}")
            if r['install']['state'] == 'failed':
                print('\n'.join(l for l in r['install']['logTail'].splitlines() if ' ERROR ' in l or 'Error' in l)[-1500:])
        print('\n'.join(res['portWarnings'][:20]))
        state = {r['name']: r['install']['state'] for r in res['rows']}
        assert res['base']['state'] == 'succeeded', res['base']['logTail'][-3000:]
        assert state['legacy_sales_custom'] == 'succeeded' and state['legacy_account_bridge'] == 'failed', state
        assert 'tracking=True' in (res['ported'] / 'legacy_sales_custom' / 'models' / 'sale_order.py').read_text()


if __name__ == '__main__':
    main(*sys.argv[1:])

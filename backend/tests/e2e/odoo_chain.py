"""Odoo 12 -> 19 on real Odoo + OpenUpgrade (workflow odoo-e2e.yml). Synthetic data only: Odoo's own demo data.
    python odoo_chain.py prepare | hop <version> | reconcile
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


if __name__ == '__main__':
    main(*sys.argv[1:])

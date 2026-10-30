# ruff: noqa: F821 — a script for `odoo shell` (env comes from the shell), read as text by mf.odoo.flows; never imported
# Business-flow checks run with `odoo shell` on Odoo 19 (env is the shell's environment). Each check is recorded; nothing stops
# at the first failure. Synthetic data only.
import json, traceback
RESULTS = []
def check(name, fn):
    try:
        with env.cr.savepoint():
            got = fn()
        RESULTS.append({'check': name, 'ok': True, 'got': str(got)[:200]})
    except Exception as e:  # noqa: BLE001
        RESULTS.append({'check': name, 'ok': False, 'error': f'{type(e).__name__}: {e}'[:400]})
def product():
    return env['product.product'].search([('sale_ok', '=', True), ('type', '=', 'consu')], limit=1) or env['product.product'].search([('sale_ok', '=', True)], limit=1)
def order(partner, qty, price, **vals):
    so = env['sale.order'].create(dict({'partner_id': partner.id, 'order_line': [(0, 0, {'product_id': product().id, 'product_uom_qty': qty, 'price_unit': price})]}, **vals))
    so.action_confirm()
    return so
def lot():
    l = env['stock.lot'].search([], limit=1)
    assert l, 'sin lotes demo'
    return l
def done():
    print('MF_FLOW ' + json.dumps(RESULTS))

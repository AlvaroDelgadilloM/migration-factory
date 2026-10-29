exec(open('/flows/common.py').read())
from odoo.exceptions import UserError
P = env['res.partner']
def customer360():
    a, b = P.create({'name': 'MF oro', 'x_customer_tier': 'gold', 'x_external_customer_id': 'C-MF-1'}), P.create({'name': 'MF bronce'})
    assert (a.x_score, b.x_score) == (100, 50), (a.x_score, b.x_score)
    (a | b).legacy_block()   # was @api.one
    assert {a.x_legacy_status, b.x_legacy_status} == {'blocked'}
    return 'puntaje 100/50, ambos bloqueados'
check('legacy_customer360: puntaje y bloqueo de varios clientes (antes @api.one)', customer360)
def credit():
    p = P.create({'name': 'MF crédito', 'x_credit_limit': 10000, 'x_customer_tier': 'gold'})
    order(p, 9, 1000, x_legacy_reference='LEG-MF-2')
    p.invalidate_recordset()
    assert p.x_credit_exposure == 9000 and p.x_credit_risk == 'MEDIUM', (p.x_credit_exposure, p.x_credit_risk)
    return f'exposición {p.x_credit_exposure}, riesgo {p.x_credit_risk} (función PL/pgSQL legacy_ext.calculate_risk)'
check('legacy_credit: exposición y riesgo con la función SQL externa', credit)
def contract():
    ok = P.create({'name': 'MF contrato ok', 'x_credit_limit': 100000})
    c = env['legacy.sales.contract'].create({'name': 'CT-MF-1', 'partner_id': ok.id})
    c.action_activate()
    assert c.state == 'active', c.state
    bad = P.create({'name': 'MF contrato riesgo', 'x_credit_limit': 1000})
    order(bad, 5, 1000)
    bad.invalidate_recordset()
    c2 = env['legacy.sales.contract'].create({'name': 'CT-MF-2', 'partner_id': bad.id})
    try:
        c2.action_activate()
    except UserError:
        return 'activo; el de riesgo HIGH se rechaza'
    raise AssertionError('activó un contrato con riesgo alto')
check('legacy_sales_contract: activar contrato y bloquear por riesgo', contract)
def invoice():
    p = P.create({'name': 'MF factura'})
    inv = env['account.move'].create({'move_type': 'out_invoice', 'partner_id': p.id, 'x_external_invoice_id': 'EXT-MF-2',
                                      'invoice_line_ids': [(0, 0, {'product_id': product().id, 'quantity': 2, 'price_unit': 750})]})
    inv.action_post()
    env.cr.execute('select legacy_number, amount_total from legacy_ext.invoice_audit where invoice_odoo_id = %s', (inv.id,))
    row = env.cr.fetchone()
    assert inv.state == 'posted' and row and row[0] == inv.name and float(row[1]) == inv.amount_total, (inv.state, row)
    return row
check('legacy_account_bridge: publicar factura registra la auditoría externa', invoice)
def integration():
    env['legacy.integration.service'].enqueue('ORDER_EXPORT', 'SO-MF-1', {'amount': 1})
    msgs = env['legacy.integration.message'].search([('external_id', '=', 'SO-MF-1')])
    assert len(msgs) == 1 and msgs.status == 'PENDING', (len(msgs), msgs.status)
    env.cr.execute("update legacy_ext.integration_message set status = 'DONE' where external_id = 'SO-MF-1' returning processed_at")
    assert env.cr.fetchone()[0], 'el trigger no puso processed_at'
    return 'encolado, visible por el ORM, trigger al cerrar'
check('legacy_integration: encolar mensaje, leerlo por el ORM y trigger de procesado', integration)
def commission():
    p = P.create({'name': 'MF comisión'})
    so = order(p, 1, 1000)
    c = env['legacy.commission'].create({'user_id': env.uid, 'sale_order_id': so.id, 'period': '2026-10', 'base_amount': 1000})
    assert c.amount == 30, c.amount
    env['legacy.commission'].refresh_legacy_mv()
    env.cr.execute('select count(*) from legacy_ext.mv_commission_monthly')
    return f'comisión {c.amount}, vista materializada {env.cr.fetchone()[0]} fila(s)'
check('legacy_commission: cálculo y refresco de la vista materializada', commission)
def quality():
    l = lot()
    l.x_quality_score = 97.5
    l.action_quality_approve()
    env.cr.execute('select count(*) from legacy_ext.quality_event where lot_odoo_id = %s', (l.id,))
    n = env.cr.fetchone()[0]
    assert l.x_quality_state == 'approved' and n == 1, (l.x_quality_state, n)
    return l.name
check('legacy_inventory_quality: aprobar lote registra el evento externo', quality)
def reporting():
    rows = env['legacy.sales.report'].search_read([('legacy_reference', '=', 'LEG-MF-2')], ['customer_tier', 'amount_total'])
    assert rows and rows[0]['customer_tier'] == 'gold', rows
    return rows[0]
check('legacy_reporting: vista SQL con nivel del cliente', reporting)
done()

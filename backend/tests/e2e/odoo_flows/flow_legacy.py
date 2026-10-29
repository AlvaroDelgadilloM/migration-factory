exec(open('/flows/common.py').read())
P = env['res.partner']
def credit():
    p = P.create({'name': 'MF cliente crédito', 'x_credit_limit': 50000, 'x_legacy_segment': 'gold'})
    order(p, 10, 1200)
    p.invalidate_recordset()
    assert p.x_credit_risk == 12000, p.x_credit_risk
    return p.x_credit_risk
check('legacy_partner_credit: riesgo = suma de pedidos confirmados', credit)
def negative():
    from odoo.exceptions import ValidationError
    try:
        P.create({'name': 'MF negativo', 'x_credit_limit': -1})
    except ValidationError:
        return 'rechazado'
    raise AssertionError('aceptó un límite negativo')
check('legacy_partner_credit: límite negativo rechazado', negative)
def reset():
    a, b = P.create({'name': 'MF a', 'x_credit_limit': 10}), P.create({'name': 'MF b', 'x_credit_limit': 20})
    (a | b).legacy_reset_credit()   # was @api.one: now one call for several records
    assert (a.x_credit_limit, b.x_credit_limit) == (0, 0), (a.x_credit_limit, b.x_credit_limit)
    return 'ambos en 0'
check('legacy_partner_credit: legacy_reset_credit sobre varios registros (antes @api.one)', reset)
def sales():
    p = P.create({'name': 'MF cliente venta'})
    so = order(p, 2, 1000, x_legacy_reference='LEG-MF-1', x_priority_code='1')
    assert round(so.x_margin_snapshot, 2) == round(so.amount_total * 0.12, 2), so.x_margin_snapshot
    so.action_mark_legacy_approved()
    audit = env['legacy.sale.audit'].search([('external_ref', '=', 'LEG-MF-1')])
    assert so.x_approved_legacy and len(audit) == 1, (so.x_approved_legacy, len(audit))
    big = so.legacy_find_large_orders()
    assert so in big, 'no encontró el pedido grande'
    return f'margen {so.x_margin_snapshot}, auditoría {audit.name}, grandes {len(big)}'
check('legacy_sales_custom: margen, aprobación con auditoría y SQL de pedidos grandes', sales)
def invoice():
    p = P.create({'name': 'MF cliente factura'})
    inv = env['account.move'].create({'move_type': 'out_invoice', 'partner_id': p.id, 'x_external_invoice_id': 'EXT-MF-1',
                                      'invoice_line_ids': [(0, 0, {'product_id': product().id, 'quantity': 1, 'price_unit': 500})]})
    inv.action_post()
    assert inv.state == 'posted' and inv.x_legacy_exported, (inv.state, inv.x_legacy_exported)
    payload = inv.legacy_invoice_payload()
    assert payload['number'] == inv.name and payload['amount_total'] == inv.amount_total, payload
    return payload
check('legacy_account_bridge: publicar factura marca exportada y arma el payload', invoice)
def quality():
    l = lot()
    l.legacy_approve_quality()
    assert l.x_quality_state == 'approved' and l.x_last_legacy_check, (l.x_quality_state, l.x_last_legacy_check)
    return l.name
check('legacy_stock_lot: aprobar calidad del lote', quality)
def reporting():
    rows = env['legacy.sale.report'].search_read([('legacy_reference', '=', 'LEG-MF-1')], ['amount_total'])
    audit = env['legacy.db.audit'].search_count([])
    assert audit == 2, audit
    return f'reporte {len(rows)} fila(s), auditoría externa {audit}'
check('legacy_reporting: vista SQL de ventas y tabla externa de auditoría', reporting)
done()

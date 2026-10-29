"""Before/after reconciliation of an Odoo database migration: the same business facts read from the 12.0 database and from the
migrated one, with the queries each schema needs (invoices: account_invoice in 12.0, account_move since 13.0). A check that
cannot run on one side (module not installed) is reported as not applicable, never as equal."""
from __future__ import annotations

# {dropped}: the modules uninstalled by the migration; their records stay without XML ID afterwards, so both sides treat them alike
MODULE_XMLID = ("exists (select 1 from ir_model_data d where d.model = 'res.partner' and d.res_id = p.id "
                "and d.module not in ('__export__', '__import__'{dropped}))")
PARTNERS = f'select count(*) from res_partner p where not {MODULE_XMLID}'
PARTNERS_MODULES = f'select count(*) from res_partner p where {MODULE_XMLID}'
INFO = {'partners_modules'}
USERS = "select count(*) from res_users where login not in ('default', 'portaltemplate')"
POSTED = ('select round(coalesce(sum(l.debit), 0), 2), round(coalesce(sum(l.credit), 0), 2) from account_move_line l '
          "join account_move m on m.id = l.move_id where m.state = 'posted'")
# 12.0: bank statement lines not yet reconciled had no entry; since 14.0 every statement line is a posted entry (liquidity and
# suspense lines for its amount), so the 12.0 side adds them to be comparable
POSTED12 = ('select round(coalesce(sum(l.debit), 0) + b.s, 2), round(coalesce(sum(l.credit), 0) + b.s, 2) from account_move_line l '
            "join account_move m on m.id = l.move_id, (select coalesce(sum(abs(amount)), 0) s from account_bank_statement_line x "
            "where not exists (select 1 from account_move_line y where y.statement_line_id = x.id)) b where m.state = 'posted' group by b.s")
ATTACHMENTS = ("select count(*) from ir_attachment where res_model is not null and res_field is null and res_model not like 'ir.%' "
               "and coalesce(url, '') not like '/web/%'")

# name -> (label, query on 12.0, query on 19.0); each returns one row: count[, total]
CHECKS = {
    'partners': ('Contactos creados por usuarios o importados', PARTNERS, PARTNERS),
    # records a module created (demo or master data, with its XML ID): a newer release may drop them on purpose; shown, not a
    # difference of the migration
    'partners_modules': ('Contactos de datos de módulos (informativo)', PARTNERS_MODULES, PARTNERS_MODULES),
    # template users (default, portaltemplate) are configuration, and 18.0 removed base.default_user
    'users': ('Usuarios', USERS, USERS),
    'products': ('Productos', 'select count(*) from product_template', 'select count(*) from product_template'),
    'sale_orders': ('Pedidos de venta (número y total)', 'select count(*), round(coalesce(sum(amount_total), 0), 2) from sale_order',
                    'select count(*), round(coalesce(sum(amount_total), 0), 2) from sale_order'),
    'purchase_orders': ('Pedidos de compra (número y total)', 'select count(*), round(coalesce(sum(amount_total), 0), 2) from purchase_order',
                        'select count(*), round(coalesce(sum(amount_total), 0), 2) from purchase_order'),
    'customer_invoices': ('Facturas y notas de crédito (número y total)',
                          "select count(*), round(coalesce(sum(amount_total), 0), 2) from account_invoice",
                          "select count(*), round(coalesce(sum(amount_total), 0), 2) from account_move "
                          "where move_type in ('out_invoice', 'out_refund', 'in_invoice', 'in_refund')"),
    # posted entries only: since 13.0 a draft invoice is a draft account.move with lines (in 12.0 it had none)
    'journal_items': ('Apuntes contables publicados: débito y crédito', POSTED12, POSTED),
    'stock_moves': ('Movimientos de inventario', 'select count(*) from stock_move', 'select count(*) from stock_move'),
    'leads': ('Oportunidades CRM', 'select count(*) from crm_lead', 'select count(*) from crm_lead'),
    # files attached to business records: not field images (res_field) nor the web assets/views Odoo regenerates per version
    'attachments': ('Adjuntos de registros', ATTACHMENTS, ATTACHMENTS),
}


def run(query_before, query_after, uninstalled=(), before_12=True) -> list[dict]:
    """query_x(sql) -> tuple | None (None when the table does not exist). Returns one row per check. uninstalled: modules the
    architect removed; before_12: the source is a 12.0 database (else both sides use the 13.0+ queries)."""
    dropped = ''.join(", '" + m.replace("'", "") + "'" for m in uninstalled)
    rows = []
    for key, (label, q12, q19) in CHECKS.items():
        q12 = (q12 if before_12 else q19).replace('{dropped}', dropped)
        b, a = query_before(q12), query_after(q19.replace('{dropped}', dropped))
        if b is None or a is None:
            state = 'NOT_APPLICABLE'
        else:
            state = 'EQUAL' if [str(x) for x in b] == [str(x) for x in a] else 'DIFFERENT'
        rows.append({'check': key, 'label': label, 'before': list(b) if b is not None else None,
                     'after': list(a) if a is not None else None, 'state': state, 'info': key in INFO})
    return rows


def status(rows: list[dict]) -> str:
    states = {r['state'] for r in rows if not r.get('info')}
    return 'DIFFERENCES' if 'DIFFERENT' in states else 'EQUAL' if 'EQUAL' in states else 'NOT_EXECUTABLE'


if __name__ == '__main__':  # self-check
    q = lambda i, k: CHECKS[k][i].replace('{dropped}', '')  # noqa: E731
    before = {q(1, 'partners'): (42,), q(1, 'sale_orders'): (20, '124021.00')}
    after = {q(2, 'partners'): (42,), q(2, 'sale_orders'): (20, '124021.00')}
    rows = run(before.get, after.get)
    assert {r['check']: r['state'] for r in rows}['partners'] == 'EQUAL' and status(rows) == 'EQUAL'
    assert status(rows + [{'state': 'DIFFERENT', 'info': True}]) == 'EQUAL'
    seen = []
    run(lambda q: seen.append(q), lambda q: None, uninstalled=['legacy_x'])
    assert "'__import__', 'legacy_x')" in next(q for q in seen if 'ir_model_data' in q) and not any('{dropped}' in q for q in seen)
    print('ok')

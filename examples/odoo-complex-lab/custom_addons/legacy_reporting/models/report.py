from odoo import fields, models, tools

class LegacySalesReport(models.Model):
    _name = 'legacy.sales.report'
    _description = 'Legacy Sales Report'
    _auto = False

    order_id = fields.Many2one('sale.order', readonly=True)
    partner_id = fields.Many2one('res.partner', readonly=True)
    amount_total = fields.Float(readonly=True)
    legacy_reference = fields.Char(readonly=True)
    customer_tier = fields.Char(readonly=True)

    def init(self):
        tools.drop_view_if_exists(self._cr, 'legacy_sales_report')
        self._cr.execute("""
            CREATE OR REPLACE VIEW legacy_sales_report AS (
              SELECT
                so.id id,
                so.id order_id,
                so.partner_id partner_id,
                so.amount_total amount_total,
                so.x_legacy_reference legacy_reference,
                rp.x_customer_tier customer_tier
              FROM sale_order so
              JOIN res_partner rp ON rp.id = so.partner_id
            )
        """)

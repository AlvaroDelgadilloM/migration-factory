from odoo import fields, models, tools

class LegacySaleReport(models.Model):
    _name = 'legacy.sale.report'
    _description = 'Legacy Sales SQL Report'
    _auto = False

    order_id = fields.Many2one('sale.order', readonly=True)
    partner_id = fields.Many2one('res.partner', readonly=True)
    amount_total = fields.Float(readonly=True)
    legacy_reference = fields.Char(readonly=True)

    def init(self):
        tools.drop_view_if_exists(self._cr, 'legacy_sale_report')
        self._cr.execute("""
            CREATE OR REPLACE VIEW legacy_sale_report AS (
                SELECT
                    so.id AS id,
                    so.id AS order_id,
                    so.partner_id AS partner_id,
                    so.amount_total AS amount_total,
                    so.x_legacy_reference AS legacy_reference
                FROM sale_order so
            )
        """)

from odoo import api, fields, models

class LegacyCommission(models.Model):
    _name = 'legacy.commission'
    _description = 'Legacy Commission'

    user_id = fields.Many2one('res.users', required=True)
    sale_order_id = fields.Many2one('sale.order', required=True)
    period = fields.Char(required=True)
    base_amount = fields.Float()
    rate = fields.Float(default=0.03)
    amount = fields.Float(compute='_compute_amount', store=True)

    @api.depends('base_amount','rate')
    def _compute_amount(self):
        for rec in self:
            rec.amount = rec.base_amount * rec.rate

    @api.model
    def refresh_legacy_mv(self):
        self._cr.execute("REFRESH MATERIALIZED VIEW legacy_ext.mv_commission_monthly")

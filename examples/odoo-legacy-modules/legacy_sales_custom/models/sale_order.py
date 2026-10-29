from odoo import api, fields, models

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    x_legacy_reference = fields.Char(string='Legacy Reference', track_visibility='onchange')
    x_priority_code = fields.Selection(
        [('0', 'Normal'), ('1', 'High'), ('2', 'Critical')],
        default='0',
        track_visibility='onchange'
    )
    x_approved_legacy = fields.Boolean(string='Legacy Approved')
    x_margin_snapshot = fields.Float(compute='_compute_margin_snapshot', store=True)

    @api.multi
    @api.depends('amount_total')
    def _compute_margin_snapshot(self):
        for order in self:
            order.x_margin_snapshot = order.amount_total * 0.12

    @api.multi
    def action_mark_legacy_approved(self):
        for order in self:
            order.write({'x_approved_legacy': True})
            self.env['legacy.sale.audit'].create({
                'name': 'Approved %s' % order.name,
                'external_ref': order.x_legacy_reference,
            })
        return True

    @api.multi
    def legacy_find_large_orders(self):
        self._cr.execute(
            "SELECT id FROM sale_order WHERE amount_total > %s ORDER BY amount_total DESC",
            (1000,)
        )
        return self.browse([r[0] for r in self._cr.fetchall()])


class LegacySaleAudit(models.Model):
    _name = 'legacy.sale.audit'
    _description = 'Legacy Sale Audit'
    _order = 'id desc'

    name = fields.Char(required=True)
    external_ref = fields.Char(index=True)
    note = fields.Text()

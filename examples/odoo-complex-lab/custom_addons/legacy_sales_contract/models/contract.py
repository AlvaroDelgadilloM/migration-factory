from odoo import api, fields, models
from odoo.exceptions import UserError

class LegacySalesContract(models.Model):
    _name = 'legacy.sales.contract'
    _inherit = ['mail.thread']
    _description = 'Legacy Sales Contract'

    name = fields.Char(required=True, track_visibility='onchange')
    partner_id = fields.Many2one('res.partner', required=True)
    state = fields.Selection([
        ('draft','Draft'),('active','Active'),('suspended','Suspended'),('closed','Closed')
    ], default='draft', track_visibility='onchange')
    discount_rate = fields.Float()
    legacy_code = fields.Char(index=True)

    @api.multi
    def action_activate(self):
        for rec in self:
            if rec.partner_id.x_credit_risk in ('HIGH','BLOCKED'):
                raise UserError('Credit risk blocks activation')
            rec.state = 'active'
        return True

class SaleOrder(models.Model):
    _inherit = 'sale.order'
    x_contract_id = fields.Many2one('legacy.sales.contract')
    x_legacy_reference = fields.Char(track_visibility='onchange')

from odoo import api, fields, models
from odoo.exceptions import ValidationError

class ResPartner(models.Model):
    _inherit = 'res.partner'

    x_credit_limit = fields.Float(default=0.0)
    x_credit_risk = fields.Float(compute='_compute_credit_risk')
    x_legacy_segment = fields.Selection([
        ('standard', 'Standard'),
        ('gold', 'Gold'),
        ('vip', 'VIP'),
    ], default='standard')

    @api.multi
    def _compute_credit_risk(self):
        for partner in self:
            orders = self.env['sale.order'].search([
                ('partner_id', '=', partner.id),
                ('state', 'in', ['sale', 'done']),
            ])
            partner.x_credit_risk = sum(orders.mapped('amount_total'))

    @api.constrains('x_credit_limit')
    def _check_credit_limit(self):
        for rec in self:
            if rec.x_credit_limit < 0:
                raise ValidationError('Credit limit cannot be negative')

    @api.one
    def legacy_reset_credit(self):
        self.x_credit_limit = 0.0

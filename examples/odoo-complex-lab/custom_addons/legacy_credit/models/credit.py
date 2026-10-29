from odoo import api, fields, models

class ResPartner(models.Model):
    _inherit = 'res.partner'

    x_credit_limit = fields.Float(default=0)
    x_credit_exposure = fields.Float(compute='_compute_credit')
    x_credit_risk = fields.Char(compute='_compute_credit')

    @api.multi
    def _compute_credit(self):
        for p in self:
            orders = self.env['sale.order'].search([
                ('partner_id','=',p.id),('state','in',['sale','done'])
            ])
            exposure = sum(orders.mapped('amount_total'))
            p.x_credit_exposure = exposure
            self._cr.execute(
                "SELECT legacy_ext.calculate_risk(%s,%s)",
                (p.x_credit_limit, exposure)
            )
            p.x_credit_risk = self._cr.fetchone()[0]

from odoo import api, fields, models

class StockProductionLot(models.Model):
    _inherit = 'stock.production.lot'

    x_quality_state = fields.Selection([
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], default='pending')
    x_last_legacy_check = fields.Datetime()

    @api.multi
    def legacy_approve_quality(self):
        self.write({
            'x_quality_state': 'approved',
            'x_last_legacy_check': fields.Datetime.now(),
        })
        return True

from odoo import api, fields, models

class StockProductionLot(models.Model):
    _inherit = 'stock.production.lot'

    x_quality_state = fields.Selection([
        ('pending','Pending'),('approved','Approved'),('rejected','Rejected')
    ], default='pending', track_visibility='onchange')
    x_quality_score = fields.Float()
    x_external_batch = fields.Char()

    @api.multi
    def action_quality_approve(self):
        for lot in self:
            lot.x_quality_state = 'approved'
            self._cr.execute(
                '''INSERT INTO legacy_ext.quality_event
                   (lot_odoo_id,product_odoo_id,event_type,score)
                   VALUES (%s,%s,%s,%s)''',
                (lot.id, lot.product_id.id, 'APPROVED', lot.x_quality_score)
            )
        return True

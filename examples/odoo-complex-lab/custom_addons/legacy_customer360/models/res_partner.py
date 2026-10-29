from odoo import api, fields, models

class ResPartner(models.Model):
    _inherit = 'res.partner'

    x_external_customer_id = fields.Char(index=True, track_visibility='onchange')
    x_customer_tier = fields.Selection([
        ('bronze','Bronze'),('silver','Silver'),('gold','Gold'),('platinum','Platinum')
    ], default='bronze')
    x_legacy_status = fields.Selection([
        ('active','Active'),('blocked','Blocked'),('review','Review')
    ], default='active')
    x_score = fields.Float(compute='_compute_score')

    @api.multi
    def _compute_score(self):
        for rec in self:
            rec.x_score = 100 if rec.x_customer_tier in ('gold','platinum') else 50

    @api.one
    def legacy_block(self):
        self.x_legacy_status = 'blocked'

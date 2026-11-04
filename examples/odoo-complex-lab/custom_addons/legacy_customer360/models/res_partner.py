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

    @api.one
    def legacy_tier_label(self):
        self._cr.execute(f"SELECT name FROM {self._table} WHERE id = {self.id}")
        return '%s:%s' % (self._cr.fetchone()[0], self.x_customer_tier)

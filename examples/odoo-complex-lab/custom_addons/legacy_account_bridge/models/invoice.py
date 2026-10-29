from odoo import api, fields, models

class AccountInvoice(models.Model):
    _inherit = 'account.invoice'

    x_external_invoice_id = fields.Char(index=True)
    x_export_state = fields.Selection([
        ('pending','Pending'),('exported','Exported'),('error','Error')
    ], default='pending')

    @api.multi
    def action_invoice_open(self):
        result = super(AccountInvoice, self).action_invoice_open()
        for inv in self:
            self._cr.execute(
                '''INSERT INTO legacy_ext.invoice_audit
                   (invoice_odoo_id,external_invoice_id,legacy_number,amount_total,event_type,payload)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb)''',
                (inv.id, inv.x_external_invoice_id, inv.number,
                 inv.amount_total, 'OPEN', '{"source":"odoo12"}')
            )
        return result

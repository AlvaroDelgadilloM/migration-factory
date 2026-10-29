from odoo import api, fields, models

class AccountInvoice(models.Model):
    _inherit = 'account.invoice'

    x_external_invoice_id = fields.Char(index=True)
    x_legacy_exported = fields.Boolean(default=False)

    @api.multi
    def action_invoice_open(self):
        result = super(AccountInvoice, self).action_invoice_open()
        for invoice in self:
            if invoice.x_external_invoice_id:
                invoice.x_legacy_exported = True
        return result

    @api.multi
    def legacy_invoice_payload(self):
        self.ensure_one()
        return {
            'number': self.number,
            'date_invoice': self.date_invoice,
            'partner': self.partner_id.name,
            'amount_total': self.amount_total,
            'state': self.state,
        }

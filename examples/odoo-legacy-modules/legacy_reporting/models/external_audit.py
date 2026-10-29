from odoo import fields, models

class LegacyDbAudit(models.Model):
    _name = 'legacy.db.audit'
    _description = 'External Legacy DB Audit'
    _table = 'legacy_migration_audit'
    _auto = False

    event_type = fields.Char(readonly=True)
    reference = fields.Char(readonly=True)
    payload = fields.Text(readonly=True)
    create_date = fields.Datetime(readonly=True)

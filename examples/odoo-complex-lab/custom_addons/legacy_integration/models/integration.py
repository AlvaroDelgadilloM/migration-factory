from odoo import api, fields, models
import json

class LegacyIntegrationMessage(models.Model):
    _name = 'legacy.integration.message'
    _description = 'Legacy Integration Message'
    _table = 'integration_message'
    _auto = False

    message_type = fields.Char(readonly=True)
    external_id = fields.Char(readonly=True)
    payload = fields.Text(readonly=True)
    status = fields.Char(readonly=True)
    retry_count = fields.Integer(readonly=True)
    created_at = fields.Datetime(readonly=True)

class LegacyIntegrationService(models.AbstractModel):
    _name = 'legacy.integration.service'

    @api.model
    def enqueue(self, message_type, external_id, payload):
        self._cr.execute(
            '''INSERT INTO legacy_ext.integration_message
               (message_type,external_id,payload)
               VALUES (%s,%s,%s::jsonb)''',
            (message_type, external_id, json.dumps(payload))
        )
        return True

from odoo.tests.common import TransactionCase

class TestLegacySale(TransactionCase):
    def test_audit_create(self):
        rec = self.env['legacy.sale.audit'].create({'name': 'Test'})
        self.assertTrue(rec.id)

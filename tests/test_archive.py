import unittest
from app import workflow, server
from app.schema import new_order


class ArchiveTests(unittest.TestCase):
    def test_archive_preserves_export_and_delivery_status(self):
        order = new_order()
        original = workflow.fingerprint(order, {}, {})
        record = {'exportFingerprint': original, 'deliveredAt': '2026-11-15T10:00:00+00:00'}
        for archived in [True, False]:
            order['archived'] = archived
            self.assertEqual(workflow.fingerprint(order, {}, {}), original)
            self.assertEqual(workflow.status(order, {}, record, {}, 0)['stage'], 'delivered')
        order['site'] = 'changed'
        self.assertEqual(workflow.status(order, {}, record, {}, 0)['stage'], 'needs_export')

    def test_archive_flag_validation(self):
        for flag in [True, False]:
            server.validate(dict(new_order(), uid='archive-test', archived=flag))
        for flag in ['false', 1, None]:
            with self.assertRaises(ValueError):
                server.validate(dict(new_order(), uid='archive-test', archived=flag))

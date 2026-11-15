import unittest
from unittest.mock import patch

from app.pdf_engine import data_values, fill_meter, meter_values
from app.schema import missing, new_order


class MeterRequestTests(unittest.TestCase):
    def test_mixed_requests_render_each_row(self):
        order = new_order()
        order['meters'] = [
            {'kind': 'جديد', 'breaker': '70'},
            {'kind': 'إزالة', 'breaker': '30'},
            {'kind': 'تقوية', 'previous': '60', 'breaker': '125'},
        ]
        with patch('app.pdf_engine.put') as put:
            fill_meter(None, order, order['meters'], data_values(order))
        values = {c.kwargs['key']: c.args[1] for c in put.call_args_list}
        self.assertEqual(values['meters.0.kind'], 'جديد')
        self.assertEqual(values['meters.1.kind'], 'إزالة')
        self.assertEqual(values['meters.1.previous'], '30')
        self.assertEqual(values['meters.1.breaker'], '')
        self.assertEqual(values['meters.2.kind'], 'تقوية')
        self.assertEqual(values['meters.2.previous'], '60')
        self.assertEqual(values['meters.2.breaker'], '125')
        self.assertEqual(values['meters.2.rating'], '160')
        self.assertEqual(values['meterCount'], 2)

    def test_upgrade_missing_capacities_and_skip(self):
        order = new_order()
        order['meters'] = [{'kind': 'تقوية'}]
        questions = {q['key']: q for q in missing(order)}
        self.assertIn('الجديدة', questions['meters.0.breaker']['label'])
        self.assertIn('meters.0.previous', questions)
        order['meters'][0].update(previous='60', breaker='125')
        self.assertNotIn('meters.0.previous', {q['key'] for q in missing(order)})
        order['meters'][0]['previous'] = ''
        order['skipped'] = ['meters.0.previous']
        self.assertNotIn('meters.0.previous', {q['key'] for q in missing(order)})
        order['skipped'] = []
        for kind in ['جديد', 'إزالة']:
            order['meters'][0]['kind'] = kind
            self.assertNotIn('meters.0.previous', {q['key'] for q in missing(order)})

    def test_legacy_meter_defaults_to_new(self):
        values = meter_values({'breaker': '70'})
        self.assertEqual(values['kind'], 'جديد')
        self.assertEqual(values['breaker'], '70')
        self.assertEqual(values['previous'], '')

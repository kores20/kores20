from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz

from app import server
from app.checklists import CHECKLISTS,CHECK_COUNTS
from app.check_layouts import check_specs
from app.form_editor import ACTIVE,Overlay
from app.form_settings import check_override
from app.pdf_engine import draw_checks,template_path
from app.schema import new_order,missing


class ChecklistTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=patch.object(server,'DATA',Path(self.tmp.name));self.data.start()
        self.order=dict(new_order(),uid='checklist-a',id='264000001',works=['panel','termination','joint'])
    def tearDown(self):self.data.stop();self.tmp.cleanup()

    def test_catalog_matches_original_pdf_rows_and_keeps_indexes(self):
        self.assertEqual([r['number'] for r in CHECKLISTS['07']][:4],['1','2.1','2.2','3'])
        for tag,rows in CHECKLISTS.items():
            code='10' if tag.startswith('10-') else tag
            self.assertEqual(len(rows),CHECK_COUNTS[tag])
            with fitz.open(template_path(code)) as doc:specs=[s for s in check_specs(code,doc[0],self.order) if s['tag']==tag]
            self.assertEqual(len(specs),len(rows))
            for i,spec in enumerate(specs):
                self.assertEqual(spec['id'],f'{tag}.{i+1}')
                self.assertEqual(spec['label'],rows[i]['label'])
                self.assertIn('هل',spec['label'])
        labels={q['key']:q['label'] for q in missing(self.order)}
        self.assertIn('20 سم',labels['assessments.07.1'])
        self.assertIn('ترتيب الأطوار',labels['assessments.10-termination.11'])

    def test_local_change_overrides_global_default_and_reaches_pdf(self):
        settings={'formConfigs':{'07':{'checks':{'07.2':{'mode':'fixed','value':'نعم'}}},
            '10':{'checks':{'10-termination.12':{'mode':'fixed','value':'نعم'}}}}}
        defaults=deepcopy(settings)
        other=dict(new_order(),uid='checklist-b',id='264000002',works=['panel','termination'])
        self.order['checkOverrides']={'07.2':'لا','10-termination.12':'لا'}
        result=server.save_changes({'orders':[self.order,other],'settings':settings})
        self.assertEqual(result['statuses']['checklist-a']['fixedChecks']['07.2'],'نعم')
        self.assertEqual(check_override(settings['formConfigs']['07'],'07.2',self.order),'لا')
        self.assertEqual(check_override(settings['formConfigs']['07'],'07.2',other),'نعم')
        self.assertEqual(settings,defaults);self.assertEqual(server.get_settings()['formConfigs'],defaults['formConfigs'])
        self.assertNotIn('assessments.07.1',[q['key'] for q in missing(self.order,settings)])
        saved={o['uid']:o for o in server.state()[0]}
        self.assertEqual(saved['checklist-a']['checkOverrides']['07.2'],'لا')
        self.assertEqual(saved['checklist-b']['checkOverrides'],{})
        for tag,code,index in [('07','07',1),('10-termination','10',11)]:
            with fitz.open(template_path(code)) as doc:
                src=doc[0];spec=next(s for s in check_specs(code,src,self.order) if s['tag']==tag and s['index']==index)
                token=ACTIVE.set(Overlay(code,settings['formConfigs'][code]))
                try:
                    with patch('app.pdf_engine.tick') as tick,patch('app.pdf_engine.put'):draw_checks(None,src,code,self.order)
                finally:ACTIVE.reset(token)
            self.assertIn(tuple(spec['positions']['لا']),[call.args[1:3] for call in tick.call_args_list])
            self.assertNotIn(tuple(spec['positions']['نعم']),[call.args[1:3] for call in tick.call_args_list])

    def test_blank_na_and_restoring_default(self):
        config={'checks':{'07.1':{'mode':'fixed','value':'نعم'}}}
        for answer in ['','لا ينطبق']:
            self.order['checkOverrides']={'07.1':answer}
            self.assertEqual(check_override(config,'07.1',self.order),answer)
        self.order['checkOverrides'].clear()
        self.assertEqual(check_override(config,'07.1',self.order),'نعم')
        self.assertEqual(new_order({'checkOverrides':{'07.1':'لا'}})['checkOverrides'],{})

    def test_rejects_invalid_or_automatic_lv_overrides(self):
        for key,value in [('07.14','نعم'),('07.01','نعم'),('LV.5','لا'),('unknown.1','نعم'),('10-termination.1','wrong')]:
            with self.subTest(key=key):
                self.order['checkOverrides']={key:value}
                with self.assertRaises(ValueError):server.validate(self.order)


if __name__=='__main__':unittest.main()

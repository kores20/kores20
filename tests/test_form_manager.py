import io
import tempfile
import unittest
from unittest.mock import patch
from copy import deepcopy
from pathlib import Path

import fitz
from PIL import Image, ImageChops

from app import server
from app.form_editor import validate_config, ACTIVE, Overlay
from app.pdf_engine import describe_form, form_image, generate, draw_checks
from app.schema import new_order, selected_forms, missing, lv_checks, FORMS
from app.form_settings import custom_inputs


def order():
    o=new_order()
    o.update(uid='forms-test',id='264000001',date='2026-10-21',description='تركيب عداد',
             site='المروج',feeder='000609',permit='1216',mainBreaker='400',operator='عبدالعزيز مفرح',
             operatorId='109789',meters=[{'serial':'ECC2520842100000','breaker':'70','subscription':'000000'}])
    return o


class FormManagerTests(unittest.TestCase):
    def test_added_input_is_per_order_and_respects_required_and_visibility(self):
        o=order();o.pop('customFields')
        field={'mode':'input','label':'اسم الفني','inputType':'text','required':True,'box':[40,710,240,740],'fontSize':22}
        config={'fields':{'custom-technician':field}}
        validate_config('01',config,describe_form('01')['fields'])
        settings={'formConfigs':{'01':config}}
        key='customFields.01.custom-technician'
        self.assertEqual(custom_inputs(o,settings)[0]['label'],'اسم الفني')
        self.assertIn(key,[q['key'] for q in missing(o,settings)])
        o['customFields']={'01':{'custom-technician':'محمد'}}
        self.assertNotIn(key,[q['key'] for q in missing(o,settings)])
        other=order();self.assertEqual(other['customFields'],{})
        config['visibility']={'mode':'never'}
        self.assertEqual(custom_inputs(o,settings),[])
        self.assertNotIn(key,[q['key'] for q in missing(other,settings)])
        self.assertEqual(o['customFields']['01']['custom-technician'],'محمد')

    def test_added_input_reaches_pdf_and_preserves_leading_zeroes(self):
        o=order();box=[40,710,240,740]
        config={'fields':{'custom-reading':{'mode':'input','label':'رقم إضافي','inputType':'number','box':box,'fontSize':28}}}
        baseline=Image.open(io.BytesIO(form_image('01',config,o))).convert('RGB')
        o['customFields']={'01':{'custom-reading':'000123'}}
        server.validate(o)
        changed=Image.open(io.BytesIO(form_image('01',config,o))).convert('RGB')
        crop=tuple(round(v*1.5) for v in box)
        self.assertIsNotNone(ImageChops.difference(baseline.crop(crop),changed.crop(crop)).getbbox())
        with tempfile.TemporaryDirectory() as tmp:
            original=server.DATA;server.DATA=Path(tmp)
            try:
                server.put_orders([o]);self.assertEqual(server.state()[0][0]['customFields']['01']['custom-reading'],'000123')
            finally:server.DATA=original

    def test_rules_apply_to_existing_orders_without_rewriting_them(self):
        o=order();before=deepcopy(o)
        settings={'formConfigs':{'24':{'visibility':{'mode':'never'}},
                                '07':{'visibility':{'mode':'works','works':['meter','panel']}},
                                '08':{'visibility':{'mode':'always'}}}}
        forms=selected_forms(o,settings)
        self.assertNotIn('24',forms);self.assertIn('07',forms);self.assertIn('08',forms)
        o['works']=['excavation'];self.assertNotIn('07',selected_forms(o,settings))
        o['works']=before['works'];self.assertEqual(o,before)
        o['mode']='inspection';self.assertEqual(selected_forms(o,settings),['inspection'])
        self.assertNotIn('formConfigs',new_order(settings))

    def test_hidden_forms_and_fixed_fields_stop_requesting_unused_data(self):
        o=order();o.update(mainBreaker='',operator='',operatorId='',meters=[])
        settings={'formConfigs':{c:{'visibility':{'mode':'never'}} for c in ['24','25','16']}}
        keys=[q['key'] for q in missing(o,settings)]
        self.assertFalse(any(k in ['mainBreaker','operator','operatorId','meters'] for k in keys))
        o['representative']=''
        for c in ['01','02','LV']:
            settings['formConfigs'][c]={'fields':{'representative':{'mode':'fixed','text':'ميرزا'}}}
        self.assertNotIn('representative',[q['key'] for q in missing(o,settings)])

    def test_fixed_check_and_explicit_blank_have_no_missing_questions(self):
        o=order();o['assessments']['inspection'][0]='';o['assessments']['inspection'][1]=''
        settings={'formConfigs':{'inspection':{'checks':{'inspection.1':{'mode':'fixed','value':'لا'},
                                                       'inspection.2':{'mode':'blank'}}},
                                'LV':{'checks':{'LV.1':{'mode':'works','works':['panel'],'value':'نعم'}}}}}
        self.assertFalse(any(q['key'] in ['assessments.inspection.0','assessments.inspection.1'] for q in missing(o,settings)))
        self.assertEqual(lv_checks(o,settings)[0],'')
        o['works'].append('panel');self.assertEqual(lv_checks(o,settings)[0],'نعم')

    def test_check_value_position_size_and_blank_reach_pdf_renderer(self):
        config={'checks':{'LV.1':{'mode':'fixed','value':'لا','dx':3,'dy':2,'fontSize':16},
                          'LV.5':{'mode':'blank'}}}
        token=ACTIVE.set(Overlay('LV',config))
        try:
            with patch('app.pdf_engine.tick') as tick:draw_checks(None,None,'LV',order())
        finally:ACTIVE.reset(token)
        calls=[call.args[1:] for call in tick.call_args_list]
        self.assertIn((713,172.5,2),calls)
        self.assertFalse(any(args[1]==230.6 for args in calls))
        self.assertIn((610.6,320.9,1),calls)

    def test_all_models_have_unique_fields_and_valid_geometry(self):
        for code in FORMS:
            with self.subTest(code=code):
                meta=describe_form(code)
                ids=[f['id'] for f in meta['fields']]
                self.assertEqual(len(ids),len(set(ids)))
                for f in meta['fields']:
                    x,y,x1,y1=f['box']
                    self.assertTrue(0<=x<x1<=meta['width'] and 0<=y<y1<=meta['height'],f)

    def test_changed_text_position_and_size_are_used_in_real_preview(self):
        o=order();box=[40,710,240,740]
        baseline=Image.open(io.BytesIO(form_image('01',{},o))).convert('RGB')
        config={'fields':{'representative':{'mode':'fixed','text':'TEST FORM','box':box,'fontSize':30}}}
        changed=Image.open(io.BytesIO(form_image('01',config,o))).convert('RGB')
        crop=tuple(round(v*1.5) for v in box)
        self.assertIsNotNone(ImageChops.difference(baseline.crop(crop),changed.crop(crop)).getbbox())
        meta=describe_form('01',{'formConfigs':{'01':config}})
        f=next(f for f in meta['fields'] if f['id']=='representative')
        self.assertEqual(f['example'],'TEST FORM');self.assertEqual(f['box'],box);self.assertEqual(f['fontSize'],30)
        with fitz.open(stream=generate(o,settings={'formConfigs':{'24':{'visibility':{'mode':'never'}}}}),filetype='pdf') as d:
            self.assertEqual(len(d),9)

    def test_save_preserves_orders_other_form_configs_and_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            original=server.DATA;server.DATA=Path(tmp)
            try:
                o=order();server.put_orders([o])
                a={'fields':{'representative':{'mode':'fixed','text':'ميرزا','box':[108,405.6,225,418.6],'fontSize':28}}}
                b={'visibility':{'mode':'never'}}
                server.save_form_config('LV',a);server.save_form_config('25',b)
                orders,settings=server.state()
                self.assertEqual(orders,[o]);self.assertEqual(settings['formConfigs'],{'LV':a,'25':b})
                self.assertEqual(settings['inspector'],'علي الشهري')
                server.save_form_config('25',{})
                self.assertIn('25',selected_forms(o,server.state()[1]))
                self.assertEqual(server.state()[1]['formConfigs']['LV'],a)
            finally:server.DATA=original

    def test_invalid_rules_and_off_page_fields_are_rejected(self):
        meta=describe_form('LV')['fields']
        invalid=[{'visibility':{'mode':'works','works':[]}},
                 {'visibility':['always']}, {'visibility':{}},
                 {'fields':{'representative':{'box':[-1,20,100,50]}}},
                 {'fields':{'representative':{'fontSize':float('nan')}}},
                 {'checks':{'LV.5':{'dx':1000}}},
                 {'fields':{'unknown':{'mode':'fixed','text':'x'}}}]
        for config in invalid:
            with self.subTest(config=config),self.assertRaises(ValueError):validate_config('LV',config,meta)
        custom={'fields':{'custom-example':{'mode':'fixed','text':'نص مضاف','box':[30,30,130,50],'fontSize':22}}}
        self.assertEqual(validate_config('LV',custom,meta),custom)

if __name__=='__main__':unittest.main()

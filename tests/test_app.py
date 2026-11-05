import base64
import io
import json
import tempfile
import time
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

import fitz
from openpyxl import Workbook

from app import server
from app.pdf_engine import generate, meter_values, extras, draw_checks, data_values, fill_meter, KEYS
from app.schema import new_order, missing, selected_forms, lv_checks


def complete_order():
    o=new_order();o.update(uid=uuid.uuid4().hex,id='264000001',date='2026-10-21',description='تركيب عداد',
      site='المروج',feeder='000609',permit='1216',mainBreaker='400',voltage='400',date190='2026-10-20',
      date200='2026-10-22',operator='عبدالعزيز مفرح',operatorId='109789',
      meters=[{'serial':'ECC2520842109034','breaker':'70','subscription':'4222_0002_00_717649'}])
    return o


class SchemaTests(unittest.TestCase):
    def test_bundle_and_inspection_scope(self):
        o=complete_order();self.assertEqual(len(selected_forms(o)),10);self.assertEqual(missing(o),[])
        o['works']=['excavation','termination','transformer','rmu','earthing']
        self.assertEqual(len(selected_forms(o)),13)
        self.assertNotIn('25',selected_forms(o))
        o['mode']='inspection';self.assertEqual(selected_forms(o),['inspection'])
        self.assertFalse(any(q['section']=='العدادات' for q in missing(o)))

    def test_lv_rules_follow_work_changes_on_existing_orders(self):
        o=complete_order()
        # Old manual patterns and skipped questions must not change the fixed LV ticks.
        o['assessments']['LV']=['نعم']*14
        o['skipped']=['assessments.LV.4']
        cases=[([], {5,6}), (['meter'], {5,6,11,12,13,14}),
               (['panel'], {5,6,7,8,9,10}),
               (['meter','panel','lv'], set(range(5,15)))]
        for works,expected in cases:
            with self.subTest(works=works):
                o['works']=works
                self.assertEqual({i+1 for i,v in enumerate(lv_checks(o)) if v=='نعم'}, expected)
                self.assertEqual(selected_forms(o).count('LV'),1)
                self.assertEqual(selected_forms(o)[-1],'LV')
                self.assertFalse(any(q['key'].startswith('assessments.LV.') for q in missing(o)))

    def test_missing_and_intentionally_blank(self):
        o=complete_order();o['permit']='';self.assertIn('permit',[q['key'] for q in missing(o)])
        o['skipped']=['permit'];self.assertNotIn('permit',[q['key'] for q in missing(o)])
        o['assessments']['inspection'][2]='';self.assertIn('assessments.inspection.2',[q['key'] for q in missing(o)])

    def test_procedure_dates_are_no_longer_required(self):
        o=complete_order();o.pop('date190');o.pop('date200')
        self.assertEqual(missing(o),[])
        self.assertNotIn('date190',new_order());self.assertNotIn('date200',new_order())
        o['date']=''
        self.assertIn('date',[q['key'] for q in missing(o)])

    def test_ct_rows_not_reported_for_ordinary_meter(self):
        o=complete_order();o['assessments']['24'][3]='';o['assessments']['16'][8]='';o['assessments']['16'][5]=''
        self.assertEqual(missing(o),[])
        o['meters'][0]['ct']=True
        keys=[q['key'] for q in missing(o)]
        self.assertIn('assessments.24.3',keys);self.assertIn('meters.0.ctRatio',keys)
        self.assertIn('meters.0.rating',keys)

    def test_breaker_rating_year_and_removed_reading(self):
        self.assertEqual(meter_values({'serial':'ECC2520842109034','breaker':'100'})['rating'],'100')
        self.assertEqual(meter_values({'serial':'SMR2520828236067','breaker':'125'})['rating'],'160')
        self.assertEqual(meter_values({'serial':'SMR2520828236067'})['year'],'2025')
        self.assertEqual(meter_values({'serial':'842109034'})['year'],'')
        self.assertEqual(meter_values({'kind':'إزالة','breaker':'30','removedReading':'2006'})['removedReading'],'2006')
        ct=meter_values({'ct':True,'breaker':'200'});self.assertEqual(ct['rating'],'');self.assertEqual(ct['multiplier'],'')

    def test_voltage_varies_by_order_and_missing_voltage_is_not_assumed(self):
        self.assertEqual(new_order({'voltage':'400'})['voltage'],'')
        self.assertEqual(meter_values({})['voltage'],'')
        self.assertEqual(meter_values({},'230')['voltage'],'230')
        self.assertEqual(meter_values({},'400')['voltage'],'400')
        self.assertEqual(meter_values({'voltage':'230'},'400')['voltage'],'230')
        o=complete_order();o.pop('voltage')
        q=next(q for q in missing(o) if q['key']=='voltage');self.assertEqual(q['kind'],'voltage')
        o['meters'][0]['voltage']='230'
        self.assertNotIn('voltage',[q['key'] for q in missing(o)])
        o['meters'][0].pop('voltage');o['skipped']=['voltage']
        self.assertNotIn('voltage',[q['key'] for q in missing(o)])
        o['mode']='inspection';o['skipped']=[]
        self.assertNotIn('voltage',[q['key'] for q in missing(o)])


class PersistenceImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=server.DATA;server.DATA=Path(self.tmp.name)
    def tearDown(self):server.DATA=self.old;self.tmp.cleanup()

    def test_import_30_csv_orders_preserves_identifiers(self):
        lines=['رقم أمر العمل,رمز العمل,المدينة,الموقع,المقاول,تاريخ التنفيذ,رقم المغذي']
        lines += [f'{264000000+i},801,أبها,المروج,أفق البنية,21/10/2026,000609' for i in range(30)]
        orders=server.import_table({'content':'\n'.join(lines)});self.assertEqual(len(orders),30)
        self.assertTrue(all(o['feeder']=='000609' and o['date']=='2026-10-21' for o in orders))
        server.put_orders(orders);self.assertEqual(len(server.state()[0]),30)

    def test_import_preserves_each_orders_voltage(self):
        orders=server.import_table({'content':'رقم أمر العمل,الجهد\n264000001,230\n264000002,400'})
        self.assertEqual([o['voltage'] for o in orders],['230','400'])
        with self.assertRaises(ValueError):server.import_table({'content':'رقم أمر العمل,الجهد\n264000003,380'})

    def test_excel_and_cross_contractor_representative(self):
        wb=Workbook();ws=wb.active;ws.append(['رقم أمر العمل','رمز العمل','تاريخ التنفيذ','المقاول','رقم المغذي'])
        ws.append(['264000001','409','21/10','السرحان','000000']);b=io.BytesIO();wb.save(b)
        orders=server.import_table({'filename':'orders.xlsx','base64':base64.b64encode(b.getvalue()).decode(),'year':2026})
        self.assertEqual(orders[0]['works'],['excavation','joint']);self.assertEqual(orders[0]['feeder'],'000000')
        self.assertEqual(orders[0]['representative'],'');self.assertEqual(orders[0]['date'],'2026-10-21')

    def test_malformed_import_and_atomic_save(self):
        with self.assertRaises(ValueError):server.import_table({'content':'رقم أمر العمل,التاريخ\n264000001,30/02/2026'})
        good=complete_order();bad=complete_order();bad['id']='../../etc/passwd'
        with self.assertRaises(ValueError):server.put_orders([good,bad])
        self.assertEqual(server.state()[0],[])

    def test_upsert_keeps_other_orders(self):
        a=complete_order();b=complete_order();b['id']='264000002';server.put_orders([a,b]);a['site']='شمسان';server.put_orders([a])
        self.assertEqual(len(server.state()[0]),2)


class PDFTests(unittest.TestCase):
    def test_meter_pdf_uses_the_selected_order_voltage(self):
        o=complete_order()
        for voltage in ['230','400','']:
            with self.subTest(voltage=voltage):
                o['voltage']=voltage
                with patch('app.pdf_engine.put') as put:fill_meter(None,o,o['meters'],data_values(o))
                value=next(call.args[1] for call in put.call_args_list if call.kwargs.get('key')=='meters.0.voltage')
                self.assertEqual(value,voltage)
    def test_original_templates_and_meter_overflow(self):
        o=complete_order();raw=generate(o)
        with fitz.open(stream=raw,filetype='pdf') as doc:
            self.assertEqual(len(doc),10);self.assertGreater(doc[7].rect.width,doc[7].rect.height)
            self.assertGreater(doc[-1].rect.width,doc[-1].rect.height)
            self.assertTrue(all(len(p.get_images())>=1 for p in doc))
        o['meters']=[dict(o['meters'][0],serial=f'ECC25208421{i:05}') for i in range(11)]
        with fitz.open(stream=generate(o),filetype='pdf') as doc:self.assertEqual(len(doc),11)

    def test_lv_footer_and_actual_checked_rows(self):
        o=complete_order();o['representative']='ميرزا'
        with patch('app.pdf_engine.put') as put, patch('app.pdf_engine.tick') as tick:
            extras(None,'LV',o,data_values(o))
            draw_checks(None,None,'LV',o)
        name_calls=[call for call in put.call_args_list if call.args[1]=='ميرزا']
        self.assertEqual(len(name_calls),1)
        self.assertEqual(name_calls[0].args[2],[108,405.6,225,418.6])
        self.assertEqual([call.args[1:3] for call in tick.call_args_list],
                         [(610.6,y) for y in [230.6,245.7,320.9,336,351,366]])

    def test_saved_legacy_procedure_dates_do_not_print(self):
        o=complete_order();values=data_values(o)
        self.assertNotIn('date190Text',values)
        self.assertNotIn('date200Text',values)
        self.assertNotIn('date190Text',KEYS['24'])
        with patch('app.pdf_engine.put') as put:
            fill_meter(None,o,[],values)
        boxes=[call.args[2] for call in put.call_args_list]
        self.assertNotIn([165,544,320,560],boxes)

    def test_batch_job_zip_and_progress(self):
        a=complete_order();a['mode']='inspection';b=complete_order();b['id']='264000002';b['mode']='inspection'
        job=server.start_export([a,b],'zip');deadline=time.monotonic()+20
        while server.JOBS[job]['state']=='working' and time.monotonic()<deadline:time.sleep(.05)
        result=server.JOBS[job];self.assertEqual(result['state'],'done');self.assertEqual(result['done'],2)
        with zipfile.ZipFile(io.BytesIO(result['raw'])) as z:
            self.assertEqual(len(z.namelist()),2)
            for name in z.namelist():
                with fitz.open(stream=z.read(name),filetype='pdf') as doc:self.assertEqual(len(doc),1)

if __name__=='__main__':unittest.main()

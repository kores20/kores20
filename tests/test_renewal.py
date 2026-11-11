from copy import deepcopy
import hashlib
import io
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import fitz

from app import renewal,server,pdf_engine


class RenewalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.templates=self.root/'templates';self.templates.mkdir()
        self.patches=[patch.object(server,'DATA',self.root/'data'),patch.object(renewal,'ROOT',self.root/'renewal'),
                      patch.object(renewal,'JOBS',{}),patch.object(pdf_engine,'TEMPLATES',self.templates),patch.dict(os.environ,{'DATABASE_URL':''})]
        for p in self.patches:p.start()
        self.template=self.templates/'07.pdf'
        d=fitz.open();p=d.new_page(width=612,height=792);p.draw_rect((40,80,570,710),color=(0,0,0));p.insert_text((50,65),'NEW REVISION',fontsize=12);p.insert_text((420,700),'FORM-SEC-07',fontsize=11);d.save(self.template);d.close()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def source(self,raster=False,attachment=False):
        d=fitz.open();p=d.new_page(width=612,height=792);p.draw_rect((40,80,570,710),color=(0,0,0));p.insert_text((50,65),'OLD REVISION',fontsize=12);p.insert_text((420,700),'FORM-SEC-07',fontsize=11)
        p.insert_text((420,154),'264000001',fontsize=10,color=pdf_engine.BLUE)
        p.insert_text((130,133),'TEST DIV',fontsize=8,color=pdf_engine.BLUE)
        pdf_engine.tick(p,350,216)
        p.draw_line((365,587),(390,600),color=pdf_engine.BLUE,width=2) # Original signature needs explicit selection.
        if raster:
            image=renewal.render(p);scan=fitz.open();q=scan.new_page(width=612,height=792);buf=io.BytesIO();image.save(buf,format='PNG');q.insert_image(q.rect,stream=buf.getvalue());d.close();d=scan
        if attachment:p=d.new_page();p.insert_text((70,90),'Attachment preserved unchanged')
        raw=d.tobytes();d.close();return raw
    def wait(self,key,export=False):
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            job=renewal.get(key)
            if (job['exportState'] if export else job['state'])!='working':return job
            time.sleep(.05)
        self.fail('renewal job timed out')
    def start(self,raw):
        key=renewal.start(raw,'264000001-old.pdf',server.get_settings(),server.PDF_LOCK)
        self.assertEqual(self.wait(key)['state'],'ready');return key

    def test_native_and_raster_keep_ink_use_new_background_and_source_unchanged(self):
        for raster in [False,True]:
            with self.subTest(raster=raster):
                raw=self.source(raster);sha=hashlib.sha256(raw).hexdigest();key=self.start(raw);job=renewal.get(key);page=job['pages'][0]
                self.assertEqual(page['code'],'07');fields={f['id']:f for f in page['fields']}
                self.assertTrue(fields['id']['hasInk']);self.assertEqual(fields['id']['mode'],'image')
                self.assertEqual(fields['consultantSignature']['mode'],'blank')
                with self.assertRaises(ValueError):renewal.export(key)
                renewal.save_page(key,0,{'confirmed':True})
                with patch('app.pdf_engine.tick',wraps=pdf_engine.tick) as tick:out=renewal.export(key)
                self.assertTrue(any(call.args[1:3]==(350,216) for call in tick.call_args_list))
                with fitz.open(stream=out,filetype='pdf') as doc:
                    self.assertEqual(len(doc),1);self.assertIn('NEW REVISION',doc[0].get_text());self.assertNotIn('OLD REVISION',doc[0].get_text())
                    self.assertGreater(len(doc[0].get_images()),0)
                self.assertEqual(hashlib.sha256((job['directory']/'source.pdf').read_bytes()).hexdigest(),sha)
                self.assertEqual(len(server.state()[0]),0)

    def test_unknown_page_is_retained_and_requires_review(self):
        key=self.start(self.source(attachment=True));job=renewal.get(key)
        self.assertIsNone(job['pages'][1]['code']);self.assertTrue(job['pages'][1]['keepOriginal'])
        for i in range(2):renewal.save_page(key,i,{'confirmed':True})
        with fitz.open(stream=renewal.export(key),filetype='pdf') as doc:
            self.assertEqual(len(doc),2);self.assertIn('Attachment preserved unchanged',doc[1].get_text())

    def test_review_edits_checks_custom_crop_and_invalid_fields(self):
        key=self.start(self.source());page=deepcopy(renewal.get(key)['pages'][0]);fields=page['fields']
        field=next(f for f in fields if f['id']=='id');field.update(mode='text',text='264000009')
        check=next(f for f in fields if f['id']=='07.1');check['mode']='نعم'
        fields.append({'id':'renew-custom-demo','label':'extra','kind':'text','mode':'image','sourceBox':[40,90,130,120],'box':[40,130,130,160],'raw':True,'text':''})
        renewal.save_page(key,0,{'fields':fields})
        self.assertFalse(renewal.get(key)['pages'][0]['confirmed'])
        renewal.save_page(key,0,{'confirmed':True})
        with patch('app.pdf_engine.put',wraps=pdf_engine.put) as put,patch('app.pdf_engine.tick',wraps=pdf_engine.tick) as tick:
            renewal.export(key)
        self.assertTrue(any(call.args[1]=='264000009' for call in put.call_args_list))
        self.assertTrue(any(call.args[1:3]==(316,216) for call in tick.call_args_list))
        invalid=deepcopy(renewal.get(key)['pages'][0]['fields']);invalid[0]['box']=[-1,0,3,4]
        with self.assertRaises(ValueError):renewal.save_page(key,0,{'fields':invalid})
        invalid=deepcopy(renewal.get(key)['pages'][0]['fields']);invalid[0]['mode']='execute'
        with self.assertRaises(ValueError):renewal.save_page(key,0,{'fields':invalid})

    def test_session_and_review_survive_empty_filesystem_cache(self):
        key=self.start(self.source());renewal.save_page(key,0,{'confirmed':True});job=renewal.get(key)
        shutil.rmtree(job['directory']);renewal.JOBS.clear()
        loaded=renewal.get(key);self.assertTrue(loaded['pages'][0]['confirmed'])
        self.assertTrue(renewal.source_image(loaded,loaded['pages'][0]).is_file())
        renewal.start_export(key);self.assertEqual(self.wait(key,True)['exportState'],'done')
        self.assertTrue((loaded['directory']/'renewed.pdf').is_file())
        renewal.delete(key)
        with self.assertRaises(ValueError):renewal.get(key)

    def test_malformed_encrypted_large_and_bad_page_requests(self):
        for raw in [b'',b'not pdf',b'%PDF-not a document',b'x'*(renewal.MAX_BYTES+1)]:
            with self.assertRaises(ValueError):renewal.validate_pdf(raw)
        d=fitz.open();d.new_page();raw=d.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,owner_pw='test-only',user_pw='test-only');d.close()
        with self.assertRaises(ValueError):renewal.validate_pdf(raw)
        key=self.start(self.source())
        for index in [-1,True,3,'0']:
            with self.assertRaises(ValueError):renewal.save_page(key,index,{'confirmed':True})
        with self.assertRaises(ValueError):renewal.get('../other')

    def test_ocr_works_without_a_tesseract_executable(self):
        if not (renewal.OCR_DATA/'eng.traineddata').exists():self.skipTest('Install OCR models with python -m app.ocr_setup')
        d=fitz.open();p=d.new_page();p.insert_text((40,80),'FORM SEC 07  264000001',fontsize=20)
        image=renewal.render(p);scan=fitz.open();q=scan.new_page();buf=io.BytesIO();image.save(buf,format='PNG');q.insert_image(q.rect,stream=buf.getvalue())
        try:
            with patch.dict(os.environ,{'PATH':''}):text=renewal.ocr_text(q)
            self.assertIn('264000001',text)
        finally:d.close();scan.close()

    def test_rotated_pdf_and_expired_session(self):
        with fitz.open(stream=self.source(),filetype='pdf') as doc:
            doc[0].set_rotation(90);raw=doc.tobytes()
        key=self.start(raw);page=renewal.get(key)['pages'][0]
        self.assertEqual(page['rotation'],270)
        self.assertTrue(next(f for f in page['fields'] if f['id']=='id')['hasInk'])
        renewal.get(key)['touched']=time.time()-renewal.TTL-1
        with self.assertRaises(ValueError):renewal.get(key)

    def test_scanned_tilt_and_template_change_require_new_review(self):
        with fitz.open(stream=self.source(),filetype='pdf') as doc:image=renewal.render(doc[0]).rotate(1.5,fillcolor='white')
        scan=fitz.open();p=scan.new_page(width=612,height=792);buf=io.BytesIO();image.save(buf,format='PNG');p.insert_image(p.rect,stream=buf.getvalue());raw=scan.tobytes();scan.close()
        key=self.start(raw);page=renewal.get(key)['pages'][0]
        self.assertEqual(page['code'],'07')
        self.assertTrue(next(f for f in page['fields'] if f['id']=='id')['hasInk'])
        renewal.save_page(key,0,{'confirmed':True})
        with fitz.open(self.template) as template:
            template[0].insert_text((50,77),'CHANGED TEMPLATE');raw=template.tobytes()
        self.template.write_bytes(raw)
        with self.assertRaises(ValueError):renewal.export(key)
        updated=renewal.select_model(key,0,'07')
        self.assertFalse(updated['confirmed'])

    def test_meter_type_mark_copies_original_column_without_guessing_ct(self):
        template=fitz.open();p=template.new_page(width=612,height=792);p.insert_text((40,50),'FORM-SEC-16');template.save(self.templates/'16.pdf');template.close()
        source=fitz.open(self.templates/'16.pdf');pdf_engine.tick(source[0],225,141);raw=source.tobytes();source.close()
        key=self.start(raw);fields=deepcopy(renewal.get(key)['pages'][0]['fields'])
        automatic=next(f for f in fields if f['id']=='meterType.1')
        self.assertFalse(automatic['editableValues']);self.assertEqual(automatic['mode'],'image')
        automatic['mode']='نعم'
        with self.assertRaises(ValueError):renewal.save_page(key,0,{'fields':fields})
        renewal.save_page(key,0,{'confirmed':True})
        with patch('app.pdf_engine.tick',wraps=pdf_engine.tick) as tick:renewal.export(key)
        self.assertFalse(any(call.args[1:3]==(365,141) for call in tick.call_args_list))


if __name__=='__main__':unittest.main()

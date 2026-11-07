import base64
import io
import json
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image, ImageChops, ImageDraw

from app import server
from app.pdf_engine import describe_form, example_order, form_image, generate
from app.schema import FORMS, WORKS, new_order
from app.signing import inspect_asset, draw_asset, same_owner


def fixture(kind):
    im=Image.new('RGBA',(220,100),(255,255,255,0));d=ImageDraw.Draw(im)
    if kind=='signature':d.line([(20,65),(60,30),(90,60),(170,35),(200,70)],fill=(20,60,140,255),width=4)
    else:d.rectangle((20,15,200,85),outline=(20,80,190,255),width=4);d.text((65,40),'TEST',fill=(20,80,190,255))
    buf=io.BytesIO();im.save(buf,format='PNG');raw=buf.getvalue()
    return {'raw':raw,'metadata':inspect_asset(raw,kind)}


class SigningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=server.DATA;server.DATA=Path(self.tmp.name)/'data'
    def tearDown(self):server.DATA=self.old;self.tmp.cleanup()

    def test_private_assets_preserve_original_bytes_and_settings(self):
        for kind in ['signature','stamp']:
            asset=fixture(kind);server.store_signing_asset(kind,asset['raw'])
            self.assertEqual(server.signing_assets()[kind]['raw'],asset['raw'])
        before=server.state()[1]['inspector']
        settings=server.save_signing_config({'owner':'علي الشهري','signatureEnabled':True,'stampEnabled':True})
        self.assertEqual(settings['inspector'],before)
        self.assertNotIn('raw',server.signing_status()['assets']['signature'])
        self.assertNotIn('signing',new_order(settings))

    def test_rejects_blank_or_invalid_images(self):
        for raw in [b'bad image',b'']:
            with self.assertRaises(ValueError):inspect_asset(raw,'signature')
        for color in [(0,0,0,0),(0,0,0,255),(255,255,255,255)]:
            im=Image.new('RGBA',(80,40),color);out=io.BytesIO();im.save(out,format='PNG')
            with self.assertRaises(ValueError):inspect_asset(out.getvalue(),'signature')

    def test_signature_only_follows_its_owner_not_pdc_or_contractor(self):
        assets={'signature':fixture('signature')};o=example_order()
        o['inspector']='مفتش آخر'
        self.assertEqual(form_image('24',{},o),form_image('24',{},o,assets=assets))
        o['inspector']='علي الشهري'
        self.assertNotEqual(form_image('24',{},o),form_image('24',{},o,assets=assets))
        blank={'fields':{'inspector':{'mode':'blank'}}}
        self.assertEqual(form_image('24',blank,o),form_image('24',blank,o,assets=assets))
        # LV has contractor, PDC and optional DBL boxes; no Ali consultant signature box.
        self.assertEqual(form_image('LV',{},o),form_image('LV',{},o,assets=assets))
        self.assertTrue(same_owner('عَلِي الشَّهري','علي الشهري'))
        self.assertFalse(same_owner('محمد إبراهيم','علي الشهري'))

    def test_signature_and_stamp_change_only_their_reviewed_regions(self):
        o=example_order();assets={k:fixture(k) for k in ['signature','stamp']}
        before=Image.open(io.BytesIO(form_image('24',{},o))).convert('RGB')
        after=Image.open(io.BytesIO(form_image('24',{},o,assets=assets))).convert('RGB')
        diff=ImageChops.difference(before,after)
        meta=describe_form('24');allowed=Image.new('L',before.size,0);draw=ImageDraw.Draw(allowed)
        for field in meta['fields']:
            if field['kind']=='image':
                rect=[round(v*1.5) for v in field['box']]
                draw.rectangle([rect[0]-1,rect[1]-1,rect[2]+1,rect[3]+1],fill=255)
                self.assertIsNotNone(diff.crop(tuple(rect)).getbbox())
        outside=ImageChops.multiply(diff.convert('L'),ImageChops.invert(allowed))
        self.assertIsNone(outside.getbbox())

    def test_stamp_repeats_on_every_page_including_meter_overflow(self):
        o=example_order();o['works']=[w for w in WORKS if w!='lv'];o['meters']=[dict(o['meters'][0]) for _ in range(11)]
        stamp=fixture('stamp')
        pages=[]
        def record(page,asset,box):
            pages.append(page.number);return draw_asset(page,asset,box)
        with patch('app.signing.draw_asset',side_effect=record) as draw:
            raw=generate(o,assets={'stamp':stamp})
        with fitz.open(stream=raw,filetype='pdf') as doc:self.assertEqual(len(doc),18)
        self.assertEqual(len(draw.call_args_list),18)
        self.assertEqual(set(pages),set(range(18)))

    def test_each_model_exposes_editable_signature_and_stamp_boxes(self):
        for code in FORMS:
            fields=describe_form(code)['fields']
            self.assertEqual(sum(f['id']=='consultantStamp' for f in fields),1)
            self.assertTrue(all(f['kind']=='image' for f in fields if f['id'].startswith('consultantSignature')))
        cfg={'images':{'consultantSignature':{'box':[55,616,190,638],'mode':'auto'}}}
        server.save_form_config('24',cfg)
        self.assertEqual(server.state()[1]['formConfigs']['24'],cfg)

    def test_transfer_bundle_contains_private_images_and_restores_them(self):
        old_templates=server.TEMPLATES;server.TEMPLATES=Path(self.tmp.name)/'templates';server.TEMPLATES.mkdir()
        doc=fitz.open();doc.new_page(width=612,height=792);doc.save(server.TEMPLATES/'01.pdf');doc.close()
        assets={k:fixture(k) for k in ['signature','stamp']}
        try:
            for kind,asset in assets.items():server.store_signing_asset(kind,asset['raw'])
            raw=server.migration_archive()
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                for kind,asset in assets.items():self.assertEqual(z.read('signing/'+kind+'.png'),asset['raw'])
            with server.db() as c:c.execute('DELETE FROM signing_assets')
            server.restore_migration({'base64':base64.b64encode(raw).decode()})
            for kind,asset in assets.items():self.assertEqual(server.signing_assets()[kind]['raw'],asset['raw'])
        finally:server.TEMPLATES=old_templates

if __name__=='__main__':unittest.main()

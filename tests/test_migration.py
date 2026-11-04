import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import fitz

from app import server
from app.schema import new_order

class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=server.DATA;self.templates=server.TEMPLATES
        server.DATA=Path(self.tmp.name)/'data';server.DATA.mkdir();server.TEMPLATES=Path(self.tmp.name)/'templates'
    def tearDown(self):
        server.DATA=self.data;server.TEMPLATES=self.templates;self.tmp.cleanup()
    def archive(self):
        doc=fitz.open();doc.new_page(width=612,height=792)
        order=new_order();order.update(uid='migration-test',id='264000001')
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('templates/01.pdf',doc.tobytes())
            z.writestr('orders-backup.json',json.dumps({'orders':[order],'settings':server.DEFAULTS}))
        return buf.getvalue()
    def test_first_transfer_and_duplicate_protection(self):
        raw=self.archive();result=server.restore_migration({'base64':base64.b64encode(raw).decode()})
        self.assertEqual(result,{'templates':1,'orders':1});self.assertTrue((server.TEMPLATES/'01.pdf').is_file())
        self.assertEqual(server.state()[0][0]['id'],'264000001')
        with self.assertRaises(ValueError):server.restore_migration({'base64':base64.b64encode(raw).decode()})
    def test_archive_roundtrip_contains_no_database_or_secrets(self):
        server.restore_migration({'base64':base64.b64encode(self.archive()).decode()})
        raw=server.migration_archive()
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertEqual(set(z.namelist()),{'templates/01.pdf','orders-backup.json'})
            self.assertEqual(len(json.loads(z.read('orders-backup.json'))['orders']),1)

if __name__=='__main__':unittest.main()

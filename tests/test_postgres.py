"""Optional real PostgreSQL test, isolated in a temporary database schema.

Run with TEST_DATABASE_URL pointing to a disposable PostgreSQL instance.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from unittest.mock import patch

import fitz
from PIL import Image, ImageDraw


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Set TEST_DATABASE_URL for PostgreSQL integration')
class PostgreSQLTests(unittest.TestCase):
    def test_migration_survives_empty_cache_and_new_process(self):
        import psycopg
        from psycopg.conninfo import make_conninfo
        from app import server, pdf_engine
        from app.schema import new_order

        schema = 'forms_test_' + uuid.uuid4().hex
        url = os.environ['TEST_DATABASE_URL']
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute('CREATE SCHEMA ' + schema)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                templates = Path(tmp) / 'templates'
                env = {'DATABASE_URL': make_conninfo(url, options='-csearch_path=' + schema),
                       'DATA_DIR': tmp, 'TEMPLATE_DIR': str(templates)}
                doc = fitz.open()
                doc.new_page(width=612, height=792)
                pdf = doc.tobytes()
                doc.close()
                order = new_order()
                order.update(uid='postgres-test', id='264000001', date='2026-10-21')
                archive = io.BytesIO()
                with zipfile.ZipFile(archive, 'w') as z:
                    z.writestr('templates/01.pdf', pdf)
                    z.writestr('orders-backup.json', json.dumps({'orders': [order], 'settings': {'city': 'أبها'}}))
                with patch.dict(os.environ, env), patch.object(server, 'DATA', Path(tmp)), \
                        patch.object(server, 'TEMPLATES', templates), patch.object(pdf_engine, 'TEMPLATES', templates):
                    server.restore_migration({'base64': base64.b64encode(archive.getvalue()).decode()})
                    order['permit'] = '1216'
                    server.put_orders([order])
                    self.assertEqual(server.state()[0][0]['permit'], '1216')
                    self.assertEqual(server.state()[1]['city'], 'أبها')
                    server.save_form_config('01', {'visibility': {'mode': 'never'}})
                    image=Image.new('RGBA',(80,40),(255,255,255,0));ImageDraw.Draw(image).rectangle((8,8,72,32),outline='blue',width=3)
                    image_bytes=io.BytesIO();image.save(image_bytes,format='PNG');stamp=image_bytes.getvalue()
                    server.store_signing_asset('stamp',stamp)
                    (templates / '01.pdf').unlink()
                    # Simulate a new Render process with an empty filesystem cache.
                    code = """
import hashlib, json
from app import server, pdf_engine
orders, settings = server.state()
p = pdf_engine.template_path('01')
print(json.dumps({'permit': orders[0]['permit'], 'city': settings['city'], 'forms': settings['formConfigs'], 'sha': hashlib.sha256(p.read_bytes()).hexdigest(), 'stamp': hashlib.sha256(server.signing_assets()['stamp']['raw']).hexdigest()}))
"""
                    child = subprocess.run([sys.executable, '-c', code], env=dict(os.environ),
                                           capture_output=True, text=True, check=True)
                    self.assertEqual(json.loads(child.stdout), {'permit': '1216', 'city': 'أبها',
                                                              'forms': {'01': {'visibility': {'mode': 'never'}}},
                                                              'stamp': hashlib.sha256(stamp).hexdigest(),
                                                              'sha': hashlib.sha256(pdf).hexdigest()})
                    with zipfile.ZipFile(io.BytesIO(server.migration_archive())) as z:
                        self.assertEqual(z.read('templates/01.pdf'), pdf)
        finally:
            with psycopg.connect(url, autocommit=True) as admin:
                admin.execute('DROP SCHEMA ' + schema + ' CASCADE')


if __name__ == '__main__':
    unittest.main()

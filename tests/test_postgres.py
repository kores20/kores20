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
import shutil
import sys
import tempfile
import time
import unittest
import uuid
import zipfile
from unittest.mock import patch

import fitz
from PIL import Image, ImageDraw


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Set TEST_DATABASE_URL for PostgreSQL integration')
class PostgreSQLTests(unittest.TestCase):
    def test_pool_reuses_connections_and_recovers_closed_backend(self):
        import psycopg
        from psycopg.conninfo import make_conninfo
        from app import server, storage
        from app.schema import new_order
        schema='forms_pool_'+uuid.uuid4().hex
        url=os.environ['TEST_DATABASE_URL']
        scoped=make_conninfo(url,options='-csearch_path='+schema)
        with psycopg.connect(url,autocommit=True) as admin:admin.execute('CREATE SCHEMA '+schema)
        try:
            with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'DATABASE_URL':scoped}), patch.object(server,'DATA',Path(tmp)):
                pids=[]
                for i in range(3):
                    server.save_changes({'orders':[dict(new_order(),uid='pooled',id='264000001',permit=str(i))]})
                    with server.db() as c:pids.append(c.execute('SELECT pg_backend_pid()').fetchone()[0])
                self.assertEqual(len(set(pids)),1)
                self.assertEqual(storage._pools[scoped].get_stats()['connections_num'],1)
                # An error rolls back before another request borrows the connection.
                with self.assertRaises(psycopg.errors.UndefinedTable):
                    with server.db() as c:c.execute('SELECT * FROM not_a_real_table')
                self.assertEqual(server.state()[0][0]['permit'],'2')
                # An update from another device is read fresh despite connection reuse.
                with psycopg.connect(scoped) as external:
                    external.execute('INSERT INTO settings(id,data) VALUES(1,%s)',(json.dumps({'city':'أبها'}),))
                self.assertEqual(server.get_settings()['city'],'أبها')
                # Simulate Neon closing an idle connection; checkout reconnects safely.
                with psycopg.connect(url,autocommit=True) as admin:
                    admin.execute('SELECT pg_terminate_backend(%s)',(pids[0],))
                self.assertEqual(server.state()[0][0]['permit'],'2')
                with server.db() as c:self.assertNotEqual(c.execute('SELECT pg_backend_pid()').fetchone()[0],pids[0])
        finally:
            storage.close_pools()
            with psycopg.connect(url,autocommit=True) as admin:admin.execute('DROP SCHEMA '+schema+' CASCADE')

    def test_migration_survives_empty_cache_and_new_process(self):
        import psycopg
        from psycopg.conninfo import make_conninfo
        from app import server, pdf_engine, renewal
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
                        patch.object(server, 'TEMPLATES', templates), patch.object(pdf_engine, 'TEMPLATES', templates), \
                        patch.object(renewal,'ROOT',Path(tmp)/'renewal-cache'),patch.object(renewal,'JOBS',{}):
                    server.restore_migration({'base64': base64.b64encode(archive.getvalue()).decode()})
                    order['permit'] = '1216'
                    server.put_orders([order])
                    self.assertEqual(server.state()[0][0]['permit'], '1216')
                    self.assertEqual(server.state()[1]['city'], 'أبها')
                    server.save_form_config('01', {'visibility': {'mode': 'never'}})
                    image=Image.new('RGBA',(80,40),(255,255,255,0));ImageDraw.Draw(image).rectangle((8,8,72,32),outline='blue',width=3)
                    image_bytes=io.BytesIO();image.save(image_bytes,format='PNG');stamp=image_bytes.getvalue()
                    server.store_signing_asset('stamp',stamp)
                    from app import workflow
                    assets=server.signing_assets()
                    with server.db() as c:workflow.record_exports(c,[order],server.get_settings(c),assets,workflow.now())
                    server.set_delivery(order['uid'],'delivered')
                    old=fitz.open();old_page=old.new_page(width=612,height=792)
                    old_page.insert_text((40,50),'FORM-SEC-01');old_page.insert_text((90,248),'264000001',color=(.09,.21,.36))
                    source=old.tobytes();old.close()
                    renewal_id=renewal.start(source,'264000001-old.pdf',server.get_settings(),server.PDF_LOCK)
                    deadline=time.monotonic()+15
                    while renewal.get(renewal_id)['state']=='working' and time.monotonic()<deadline:time.sleep(.05)
                    self.assertEqual(renewal.get(renewal_id)['state'],'ready')
                    renewal.save_page(renewal_id,0,{'confirmed':True})
                    shutil.rmtree(renewal.get(renewal_id)['directory']);renewal.JOBS.clear()
                    (templates / '01.pdf').unlink()
                    # Simulate a new Render process with an empty filesystem cache.
                    code = """
import hashlib, json
from app import server, pdf_engine, renewal
orders, settings = server.state()
p = pdf_engine.template_path('01')
job=renewal.get(__import__('sys').argv[1])
renewal.source_image(job,job['pages'][0])
print(json.dumps({'permit': orders[0]['permit'], 'city': settings['city'], 'forms': settings['formConfigs'], 'sha': hashlib.sha256(p.read_bytes()).hexdigest(), 'stamp': hashlib.sha256(server.signing_assets()['stamp']['raw']).hexdigest(), 'stage': server.status_batch(orders,settings)[orders[0]['uid']]['workflow']['stage'], 'renewalReviewed':job['pages'][0]['confirmed'],'renewalSource':hashlib.sha256((job['directory']/'source.pdf').read_bytes()).hexdigest()}))
"""
                    child = subprocess.run([sys.executable, '-c', code, renewal_id], env=dict(os.environ),
                                           capture_output=True, text=True, check=True)
                    self.assertEqual(json.loads(child.stdout), {'permit': '1216', 'city': 'أبها',
                                                              'forms': {'01': {'visibility': {'mode': 'never'}}},
                                                              'stage':'delivered',
                                                              'renewalReviewed':True,
                                                              'renewalSource':hashlib.sha256(source).hexdigest(),
                                                              'stamp': hashlib.sha256(stamp).hexdigest(),
                                                              'sha': hashlib.sha256(pdf).hexdigest()})
                    with zipfile.ZipFile(io.BytesIO(server.migration_archive())) as z:
                        self.assertEqual(z.read('templates/01.pdf'), pdf)
        finally:
            with psycopg.connect(url, autocommit=True) as admin:
                admin.execute('DROP SCHEMA ' + schema + ' CASCADE')


if __name__ == '__main__':
    unittest.main()

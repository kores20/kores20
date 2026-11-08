import base64
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

import fitz

from app import server,workflow
from app.schema import new_order


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data_patch=patch.object(server,'DATA',Path(self.tmp.name)/'data');self.data_patch.start()
        self.o=dict(new_order(),uid='tracked-order',id='264000001',date='2026-11-08',mode='inspection',description='حفر وتمديد',permit='1216')
        server.put_orders([self.o])
    def tearDown(self):self.data_patch.stop();self.tmp.cleanup()
    def current(self):return server.status_batch([self.o],server.get_settings())[self.o['uid']]['workflow']
    def exported(self,order=None,started='2026-11-08T08:00:00+00:00',assets=None,settings=None):
        with server.db() as c:workflow.record_exports(c,[order or self.o],settings or server.get_settings(c),assets or {},started)
    def wait_job(self,key):
        deadline=time.monotonic()+10
        while server.JOBS[key]['state']=='working' and time.monotonic()<deadline:time.sleep(.02)
        return server.JOBS[key]

    def test_ready_export_deliver_edit_and_reexport(self):
        self.assertEqual(self.current()['stage'],'ready')
        with self.assertRaises(ValueError):server.set_delivery(self.o['uid'],'delivered')
        self.exported();self.assertEqual(self.current()['stage'],'exported')
        saved=server.set_delivery(self.o['uid'],'delivered')['workflow']
        self.assertEqual(saved['stage'],'delivered');self.assertTrue(saved['deliveredAt'])
        # A normal autosave must not erase the export/delivery record.
        self.assertEqual(server.save_changes({'orders':[self.o]})['statuses'][self.o['uid']]['workflow']['stage'],'delivered')
        self.o['permit']='1217';server.put_orders([self.o]);self.assertEqual(self.current()['stage'],'needs_export')
        with self.assertRaises(ValueError):server.set_delivery(self.o['uid'],'delivered')
        self.exported(started='2026-11-08T09:00:00+00:00');self.assertEqual(self.current()['stage'],'exported')
        self.assertEqual(self.current()['deliveredAt'],'')
        server.set_delivery(self.o['uid'],'delivered');server.set_delivery(self.o['uid'],'exported')
        self.assertEqual(self.current()['stage'],'exported')

    def test_missing_data_and_print_configuration_changes(self):
        self.o['permit']='';server.put_orders([self.o]);self.assertEqual(self.current()['stage'],'draft')
        self.exported();self.assertEqual(self.current()['stage'],'exported')
        settings=deepcopy(server.DEFAULTS);settings['city']='خميس مشيط'
        server.save_changes({'settings':settings})
        # Defaults for new orders do not change an existing order's output.
        self.assertEqual(self.current()['stage'],'exported')
        settings['formConfigs']={'inspection':{'fields':{'inspector':{'mode':'blank'}}}}
        server.save_changes({'settings':settings});self.assertEqual(self.current()['stage'],'needs_export')

    def test_only_relevant_signature_assets_invalidate_output(self):
        settings=server.get_settings();record={'exportFingerprint':workflow.fingerprint(self.o,settings,{})}
        faisal={'signature-faisal':{'version':'new-faisal-signature'}}
        self.assertEqual(workflow.status(self.o,settings,record,faisal,0)['stage'],'exported')
        self.assertEqual(workflow.status(self.o,settings,record,{'signature-ali':{'version':'new-ali-signature'}},0)['stage'],'needs_export')
        self.assertEqual(workflow.status(self.o,settings,record,{'stamp':{'version':'new-stamp'}},0)['stage'],'needs_export')

    def test_late_old_job_cannot_replace_newer_export(self):
        old=deepcopy(self.o);self.o['permit']='9999';server.put_orders([self.o])
        self.exported(started='2026-11-08T09:00:00+00:00')
        self.exported(old,started='2026-11-08T08:00:00+00:00')
        self.assertEqual(self.current()['stage'],'exported')

    def test_edit_during_generation_tracks_export_snapshot(self):
        entered=threading.Event();release=threading.Event()
        def generate(*args,**kwargs):entered.set();release.wait(5);return b'test PDF'
        with patch.object(server,'generate',side_effect=generate):
            key=server.start_export([self.o],'pdf');self.assertTrue(entered.wait(2))
            self.o['permit']='8888';server.put_orders([self.o]);release.set()
            self.assertEqual(self.wait_job(key)['state'],'done')
        self.assertEqual(self.current()['stage'],'needs_export')

    def test_failed_export_and_deleted_order_do_not_get_export_status(self):
        with patch.object(server,'generate',side_effect=ValueError('intentional test failure')),patch.object(server.traceback,'print_exc'):
            key=server.start_export([self.o],'pdf');self.assertEqual(self.wait_job(key)['state'],'error')
        self.assertEqual(self.current()['stage'],'ready')
        with server.db() as c:
            c.execute('DELETE FROM orders WHERE uid=?',(self.o['uid'],))
            workflow.record_exports(c,[self.o],server.DEFAULTS,{},workflow.now())
            self.assertEqual(c.execute('SELECT count(*) FROM order_progress').fetchone()[0],0)

    def test_transfer_and_json_restore_keep_tracking(self):
        self.exported();server.set_delivery(self.o['uid'],'delivered')
        with server.db() as c:progress=workflow.context(c)[0]
        server.save_changes({'orders':[self.o],'progress':progress})
        self.assertEqual(self.current()['stage'],'delivered')
        templates=Path(self.tmp.name)/'templates';templates.mkdir()
        doc=fitz.open();doc.new_page(width=612,height=792);doc.save(templates/'01.pdf');doc.close()
        with patch.object(server,'TEMPLATES',templates):raw=server.migration_archive()
        with zipfile.ZipFile(io.BytesIO(raw)) as z:self.assertEqual(json.loads(z.read('orders-backup.json'))['progress'],progress)
        with server.db() as c:c.execute('DELETE FROM orders');c.execute('DELETE FROM order_progress')
        with patch.object(server,'TEMPLATES',templates):server.restore_migration({'base64':base64.b64encode(raw).decode()})
        self.assertEqual(self.current()['stage'],'delivered')
        with self.assertRaises(ValueError):server.save_changes({'orders':[self.o],'progress':{'other-order':{}}})


if __name__=='__main__':unittest.main()

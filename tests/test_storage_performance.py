"""Protect the save path from repeated schema setup and full-order reads."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app import server, storage
from app.schema import new_order


class SavePerformanceTests(unittest.TestCase):
    def test_warm_batch_uses_one_connection_without_schema_or_order_reads(self):
        connect=sqlite3.connect
        statements=[]
        def traced(*args,**kwargs):
            connection=connect(*args,**kwargs)
            connection.set_trace_callback(statements.append)
            return connection
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'DATABASE_URL':''}), \
                patch.object(server,'DATA',Path(tmp)), patch.object(storage.sqlite3,'connect',side_effect=traced) as connections:
            server.state()  # One-time schema initialization.
            statements.clear();connections.reset_mock()
            orders=[dict(new_order(),uid=f'batch-{i}',id=str(264000000+i),feeder='000609') for i in range(30)]
            result=server.save_changes({'orders':orders})
            self.assertEqual(connections.call_count,1)
            self.assertEqual(len(result['statuses']),30)
            self.assertFalse(any('CREATE TABLE' in sql or 'PRAGMA' in sql or 'FROM orders' in sql for sql in statements))
            # Settings, progress records and image metadata; never image bytes or
            # a query per order, even for a batch of 30.
            self.assertEqual(sum(sql.startswith('SELECT') for sql in statements),3)
            self.assertEqual(len(server.state()[0]),30)
            self.assertEqual(result['statuses']['batch-0'],server.order_status(orders[0],server.DEFAULTS))

    def test_remote_settings_stay_fresh_and_failed_batch_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'DATABASE_URL':''}), patch.object(server,'DATA',Path(tmp)):
            first=dict(new_order(),uid='first',id='264000001')
            server.save_changes({'orders':[first]})
            settings=dict(server.DEFAULTS,city='خميس مشيط')
            server.save_changes({'settings':settings})
            self.assertEqual(server.get_settings()['city'],'خميس مشيط')
            edited=dict(first,site='تعديل يجب ألا يحفظ')
            invalid=dict(first,uid='invalid',id='12')
            with self.assertRaises(ValueError):server.save_changes({'orders':[edited,invalid]})
            self.assertNotEqual(server.state()[0][0].get('site'),edited['site'])


if __name__=='__main__':unittest.main()

"""SQLite locally, PostgreSQL for a shared site with durable cloud data."""
import os
import sqlite3
import atexit
import threading
from pathlib import Path

_lock=threading.Lock()
_pools={}
_initialized=set()


def close_pools():
    """Release remote connections on shutdown (also useful for isolated tests)."""
    with _lock:
        pools=list(_pools.values());_pools.clear();_initialized.clear()
    for pool in pools:pool.close()


atexit.register(close_pools)


class Connection:
    def __init__(self, connection, postgres=False, pool=None):
        self.connection=connection;self.postgres=postgres;self.pool=pool
    def execute(self, sql, parameters=()):
        if self.postgres:sql=sql.replace('?','%s')
        return self.connection.execute(sql,parameters)
    def executemany(self,sql,parameters):
        if self.postgres:sql=sql.replace('?','%s')
        cursor=self.connection.cursor()
        try:cursor.executemany(sql,parameters)
        finally:cursor.close()
    def close(self):
        if self.pool:self.pool.putconn(self.connection)
        else:self.connection.close()
    def __enter__(self):return self
    def __exit__(self,typ,value,tb):
        try:
            if typ:self.connection.rollback()
            else:self.connection.commit()
        finally:self.close()


def open_db(directory):
    url=os.getenv('DATABASE_URL','').strip()
    if url:
        from psycopg_pool import ConnectionPool
        with _lock:
            if url not in _pools:
                # No periodic queries or permanent minimum: Neon can still sleep.
                _pools[url]=ConnectionPool(url,open=True,min_size=0,max_size=4,max_idle=60,
                    max_lifetime=600,timeout=15,max_waiting=32,
                    kwargs={'connect_timeout':15},check=ConnectionPool.check_connection)
            pool=_pools[url]
        c=Connection(pool.getconn(),True,pool)
        key=('postgres',url)
    else:
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        path=directory/'orders.sqlite3'
        c=Connection(sqlite3.connect(path,timeout=20))
        stat=path.stat();key=('sqlite',str(path.resolve()),stat.st_dev,stat.st_ino)
    try:
        # Schema checks used to run on every read and autosave over the network.
        with _lock:
            if key not in _initialized:
                c.execute("CREATE TABLE IF NOT EXISTS orders (uid TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT '')")
                c.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
                c.execute('CREATE TABLE IF NOT EXISTS templates (code TEXT PRIMARY KEY, pdf BYTEA NOT NULL)')
                c.execute('CREATE TABLE IF NOT EXISTS signing_assets (kind TEXT PRIMARY KEY, data BYTEA NOT NULL, metadata TEXT NOT NULL)')
                if not c.postgres and 'updated_at' not in [r[1] for r in c.execute('PRAGMA table_info(orders)')]:
                    c.execute("ALTER TABLE orders ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
                c.connection.commit();_initialized.add(key)
    except Exception:
        try:c.connection.rollback()
        finally:c.close()
        raise
    return c


def read_template(code):
    directory=os.getenv('DATA_DIR',str(Path(__file__).resolve().parent.parent/'.data'))
    with open_db(directory) as c:row=c.execute('SELECT pdf FROM templates WHERE code=?',(code,)).fetchone()
    return bytes(row[0]) if row else None

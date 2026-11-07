"""SQLite locally, PostgreSQL for a shared site with durable cloud data."""
import os
import sqlite3
from pathlib import Path


class Connection:
    def __init__(self, connection, postgres=False):
        self.connection=connection;self.postgres=postgres
    def execute(self, sql, parameters=()):
        if self.postgres:sql=sql.replace('?','%s')
        return self.connection.execute(sql,parameters)
    def __enter__(self):return self
    def __exit__(self,typ,value,tb):
        try:
            if typ:self.connection.rollback()
            else:self.connection.commit()
        finally:self.connection.close()


def open_db(directory):
    url=os.getenv('DATABASE_URL','').strip()
    if url:
        import psycopg
        c=Connection(psycopg.connect(url,connect_timeout=15),True)
    else:
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        c=Connection(sqlite3.connect(directory/'orders.sqlite3',timeout=20))
    try:
        c.execute("CREATE TABLE IF NOT EXISTS orders (uid TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT '')")
        c.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS templates (code TEXT PRIMARY KEY, pdf BYTEA NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS signing_assets (kind TEXT PRIMARY KEY, data BYTEA NOT NULL, metadata TEXT NOT NULL)')
        if not c.postgres and 'updated_at' not in [r[1] for r in c.execute('PRAGMA table_info(orders)')]:
            c.execute("ALTER TABLE orders ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
        c.connection.commit()
    except Exception:
        c.connection.close();raise
    return c


def read_template(code):
    directory=os.getenv('DATA_DIR',str(Path(__file__).resolve().parent.parent/'.data'))
    with open_db(directory) as c:row=c.execute('SELECT pdf FROM templates WHERE code=?',(code,)).fetchone()
    return bytes(row[0]) if row else None

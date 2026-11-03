"""Copies a Krein SQLite database into an empty PostgreSQL database (the move to Cloud SQL).

    DATABASE_URL=postgresql://user:password@host:5432/krein python -m scripts.sqlite_to_postgres [data/krein.db]

Every row is copied with its own id, so links between records, invoice numbers and the audit hash chain stay exactly as
they are. The target must be empty: the script creates the tables, copies, and then checks the row count of every table.
Uploaded files (data/uploads) are not in the database; copy that folder to the new uploads location separately.
"""
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(source=ROOT / 'data' / 'krein.db'):
    url = os.environ.get('DATABASE_URL', '').strip()
    if not url:
        raise SystemExit('Set DATABASE_URL to the PostgreSQL database to copy into.')
    source = Path(source)
    if not source.exists():
        raise SystemExit(f'{source} does not exist.')
    import psycopg
    from app import pg
    from app.migrations import POSTGRES_BASELINE

    src = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
    src.row_factory = sqlite3.Row
    version = src.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
    if version != POSTGRES_BASELINE:
        raise SystemExit(f'{source} is at database version {version}, PostgreSQL starts at {POSTGRES_BASELINE}. '
                         'Start Krein once on this SQLite file (without DATABASE_URL) so it is brought up to date, then copy.')

    database = pg.database(url)
    pg.create_schema(database)
    order = re.findall(r'^CREATE TABLE (\w+) \(', pg.SCHEMA_FILE.read_text(encoding='utf-8'), re.M)   # parents before children
    with psycopg.connect(url) as dst:
        if dst.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            raise SystemExit('The PostgreSQL database already has data. Copy into an empty database only.')
        types = {}
        for table, column, data_type in dst.execute("SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema=current_schema()"):
            types.setdefault(table, {})[column] = data_type
        report = []
        for table in order:
            columns = [c[1] for c in src.execute(f'PRAGMA table_info("{table}")') if c[1] in types[table]]
            rows = src.execute(f'SELECT {",".join(columns)} FROM "{table}" ORDER BY rowid').fetchall()
            kinds = [types[table][c] for c in columns]
            values = [tuple(_value(row[i], kinds[i], table, columns[i]) for i in range(len(columns))) for row in rows]
            if values:
                with dst.cursor() as cur:
                    cur.executemany(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join(["%s"] * len(columns))})', values)
            if 'id' in types[table]:   # new records continue after the highest copied id
                dst.execute(f"SELECT setval(pg_get_serial_sequence('{table}','id'), COALESCE(MAX(id),1), MAX(id) IS NOT NULL) FROM {table}")
            copied = dst.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            if copied != len(rows):
                raise SystemExit(f'{table}: {len(rows)} rows in SQLite but {copied} in PostgreSQL. Nothing was saved.')
            report.append((table, copied))
        dst.commit()
    src.close()
    for table, count in report:
        if count:
            print(f'{table:32} {count:>8}')
    print(f'Copied {sum(c for _, c in report)} rows in {len(report)} tables. Every count matches.')


def _value(value, data_type, table, column):
    """SQLite accepts any value in any column; PostgreSQL checks the type, so convert the few loose ones."""
    if value is None:
        return None
    if data_type == 'bigint' and not isinstance(value, int):
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and re.fullmatch(r'-?\d+', value.strip()):
            return int(value)
        raise SystemExit(f'{table}.{column}: {value!r} is not a whole number. Fix it in SQLite first.')
    if data_type == 'double precision' and isinstance(value, str):
        return float(value)
    if data_type in ('text', 'USER-DEFINED') and not isinstance(value, str):
        return str(value)
    if data_type == 'bytea' and isinstance(value, str):
        return value.encode()
    return value


if __name__ == '__main__':
    main(*sys.argv[1:2])

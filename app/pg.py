"""PostgreSQL support (used when DATABASE_URL is set, for example Cloud SQL on Google Cloud).

The app was written for SQLite, so this module gives it a PostgreSQL connection that behaves like a sqlite3 connection:
the same execute() / fetchone() / lastrowid / row['column'] / commit() / "with connect() as conn" use, and each SQL
statement is translated on the fly from the SQLite dialect the code is written in:

  ?                              -> %s (parameters), and a literal % -> %%
  "text"                         -> 'text' (SQLite reads a double-quoted word that is not a column as a string)
  x IS ? / x IS NOT ?            -> IS NOT DISTINCT FROM / IS DISTINCT FROM
  ? IS NULL                      -> CAST(? AS TEXT) IS NULL (PostgreSQL needs to know the parameter's type)
  LIKE                           -> ILIKE (SQLite's LIKE ignores upper and lower case)
  x=? COLLATE NOCASE             -> lower(x)=lower(?)
  group_concat(x[, sep])         -> string_agg(CAST(x AS TEXT), sep)
  printf('%.4f', x)              -> to_char(x, 'FM...0.0000')
  json_each(?) of a list of ids  -> json_array_elements_text
  INSERT OR IGNORE / OR REPLACE  -> INSERT ... ON CONFLICT DO NOTHING / DO UPDATE
  INSERT into a table with an id -> ... RETURNING id, so cursor.lastrowid works

Writes are serialised like in SQLite (one writer at a time, with a transaction-level advisory lock), so code that reads
and then writes, such as the audit hash chain and invoice numbering, keeps its guarantees. Every write runs inside a
savepoint, so a failed statement (for example a caught IntegrityError) does not abort the rest of the transaction, as
in SQLite. PostgreSQL errors are raised as the matching sqlite3 errors, so the existing error handling keeps working.
"""
import re
import sqlite3
import threading
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import psycopg
from psycopg.adapt import Dumper, Loader
from psycopg_pool import ConnectionPool

WRITE_LOCK = 7310   # any fixed number: the key of the advisory lock that stands in for SQLite's single writer
SCHEMA_FILE = Path(__file__).resolve().parent / 'schema_postgres.sql'


# ---- values

class UnknownTypeDumper(Dumper):
    """Python int and bool are sent untyped, as SQLite would compare them: PostgreSQL then reads the value as whatever
    the column needs (an integer column, a text column such as a period, or a 0/1 flag)."""
    oid = 0

    def dump(self, obj):
        return str(int(obj)).encode()


class NumberLoader(Loader):
    """SUM() and AVG() return NUMERIC in PostgreSQL; SQLite returns an int or a float, and the app expects that."""
    def load(self, data):
        value = Decimal(bytes(data).decode())
        return int(value) if value == value.to_integral_value() else float(value)


def _configure(conn):
    conn.adapters.register_dumper(int, UnknownTypeDumper)
    conn.adapters.register_dumper(bool, UnknownTypeDumper)
    conn.adapters.register_loader('numeric', NumberLoader)


# ---- rows

class Row:
    """A result row like sqlite3.Row: row['name'] (any case), row[0], dict(row), tuple(row)."""
    __slots__ = ('_values', '_index', '_names')

    def __init__(self, values, index, names):
        self._values, self._index, self._names = values, index, names

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return self._values[self._index[key.lower()]]
            except KeyError:
                raise IndexError('No item with that key') from None
        return self._values[key]

    def keys(self):
        return list(self._names)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __eq__(self, other):
        return isinstance(other, Row) and self._names == other._names and self._values == other._values

    def __hash__(self):
        return hash((self._names, self._values))

    def __repr__(self):
        return f'<Row {dict(zip(self._names, self._values))}>'


def _row_factory(cursor):
    names = tuple(c.name for c in cursor.description or ())
    index = {name.lower(): i for i, name in reversed(list(enumerate(names)))}   # the first column wins, as in SQLite
    return lambda values: Row(tuple(values), index, names)


# ---- SQL translation

_TOKEN = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*")


def _split_args(text):
    """Splits a function's argument text on its top-level commas."""
    args, depth, current = [], 0, ''
    for ch in text:
        if ch == ',' and depth == 0:
            args.append(current)
            current = ''
            continue
        depth += ch == '('
        depth -= ch == ')'
        current += ch
    args.append(current)
    return [a.strip() for a in args]


def _replace_function(sql, name, build):
    """Replaces every name(...) call, with balanced parentheses, by build(args)."""
    pattern = re.compile(r'\b' + name + r'\s*\(', re.I)
    while True:
        m = pattern.search(sql)
        if not m:
            return sql
        depth, i = 1, m.end()
        while depth:
            depth += sql[i] == '('
            depth -= sql[i] == ')'
            i += 1
        sql = sql[:m.start()] + build(_split_args(sql[m.end():i - 1])) + sql[i:]


# SQLite trigger name -> (table, PostgreSQL trigger): in PostgreSQL one trigger often covers both update and delete
TRIGGERS = {'ledger_no_update': ('ledger', 'ledger_no_change'), 'ledger_no_delete': ('ledger', 'ledger_no_change'),
            'audit_no_update': ('audit', 'audit_no_change'), 'audit_no_delete': ('audit', 'audit_no_change'),
            'invoice_no_update': ('invoices', 'invoice_no_update'), 'invoice_no_delete': ('invoices', 'invoice_no_delete'),
            'rent_no_update': ('rent_versions', 'rent_no_change'), 'rent_no_delete': ('rent_versions', 'rent_no_change'),
            'portal_message_no_update': ('portal_messages', 'portal_message_no_update'), 'portal_message_no_delete': ('portal_messages', 'portal_message_no_delete'),
            'repair_update_no_update': ('repair_updates', 'repair_update_no_update'), 'repair_update_no_delete': ('repair_updates', 'repair_update_no_delete'),
            'credit_note_no_delete': ('credit_notes', 'credit_note_no_delete'), 'owner_statement_frozen': ('owner_statements', 'owner_statement_frozen'),
            'invoice_revision_no_update': ('invoice_revisions', 'invoice_revision_no_change'), 'invoice_revision_no_delete': ('invoice_revisions', 'invoice_revision_no_change'),
            'contract_identity_locked': ('contracts', 'contract_identity_locked')}


def _drop_trigger(m):
    table, name = TRIGGERS[m.group(2)]
    return f'DROP TRIGGER IF EXISTS {name} ON {table}'


@lru_cache(maxsize=4096)
def translate(sql):
    """Returns (PostgreSQL statement, kind): kind is 'insert:<table>', 'replace:<table>' or 'other'."""
    literals = []

    def keep(m):
        text = m.group(0)
        if text.startswith('--'):
            return ' '
        if text.startswith('"'):   # SQLite takes a double-quoted word that is not a column as a string
            text = "'" + text[1:-1].replace('""', '"').replace("'", "''") + "'"
        literals.append(text.replace('%', '%%'))
        return f'\x00{len(literals) - 1}\x00'

    code = _TOKEN.sub(keep, sql).replace('%', '%%')
    code = re.sub(r'^\s*DROP\s+TRIGGER\s+(IF\s+EXISTS\s+)?(\w+)\s*;?\s*$', _drop_trigger, code, flags=re.I)
    code = re.sub(r'\bIS\s+NOT\s+\?', 'IS DISTINCT FROM ?', code, flags=re.I)
    code = re.sub(r'\bIS\s+\?', 'IS NOT DISTINCT FROM ?', code, flags=re.I)
    code = re.sub(r'\?(\s+IS\s+(?:NOT\s+)?NULL)', r'CAST(? AS TEXT)\1', code, flags=re.I)
    code = re.sub(r'([\w.]+)\s*=\s*\?\s+COLLATE\s+NOCASE', r'lower(\1)=lower(?)', code, flags=re.I)
    code = re.sub(r'\bLIKE\b', 'ILIKE', code, flags=re.I)
    code = re.sub(r'SELECT\s+value\s+FROM\s+json_each\(\?\)', 'SELECT CAST(j.value AS BIGINT) FROM json_array_elements_text(CAST(? AS json)) AS j(value)', code, flags=re.I)
    code = _replace_function(code, 'group_concat', lambda a: f"string_agg(CAST({a[0]} AS TEXT), {a[1] if len(a) > 1 else chr(39) + ',' + chr(39)})")

    def printf(args):
        fmt = literals[int(args[0].strip('\x00'))] if args[0].startswith('\x00') else ''
        m = re.fullmatch(r"'%%\.(\d)f'", fmt)
        if not m:
            raise ValueError('printf() is not supported on PostgreSQL: ' + sql)
        return f"to_char({args[1]}, 'FM9999999990.{'0' * int(m.group(1))}')"
    code = _replace_function(code, 'printf', printf)
    code = _replace_function(code, 'char', lambda a: '(' + '||'.join(f'chr({x})' for x in a) + ')')
    code = code.replace('?', '%s')

    kind = 'other'
    m = re.match(r'\s*INSERT\s+(OR\s+(IGNORE|REPLACE)\s+)?INTO\s+(\w+)', code, re.I)
    if m:
        table = m.group(3).lower()
        mode = (m.group(2) or '').upper()
        code = code[:m.start(1)] + code[m.end(1):] if m.group(1) else code
        if mode == 'IGNORE':
            code = code.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
        kind = ('replace:' if mode == 'REPLACE' else 'insert:') + table
    code = re.sub(r'\x00(\d+)\x00', lambda m: literals[int(m.group(1))], code)
    return code, kind


# ---- table facts (for INSERT OR REPLACE and lastrowid)

_tables = {}
_tables_lock = threading.Lock()


def _table_facts(conn):
    with _tables_lock:
        if not _tables:   # read once the tables exist; every database (and test schema) has the same tables
            cur = conn.cursor()
            cur.execute("""SELECT c.table_name, c.column_name, c.ordinal_position,
                                  (c.is_identity = 'YES' OR COALESCE(c.column_default, '') LIKE 'nextval(%%') AS ident,
                                  EXISTS (SELECT 1 FROM information_schema.table_constraints t JOIN information_schema.key_column_usage k
                                          ON k.constraint_name = t.constraint_name AND k.table_schema = t.table_schema AND k.table_name = t.table_name
                                          WHERE t.constraint_type = 'PRIMARY KEY' AND t.table_schema = c.table_schema AND t.table_name = c.table_name
                                          AND k.column_name = c.column_name) AS pk
                           FROM information_schema.columns c WHERE c.table_schema = current_schema() ORDER BY c.table_name, c.ordinal_position""")
            for table, column, _, ident, pk in cur.fetchall():
                facts = _tables.setdefault(table, {'columns': [], 'pk': [], 'identity': False})
                facts['columns'].append(column)
                if pk:
                    facts['pk'].append(column)
                if ident and column == 'id':
                    facts['identity'] = True
        return _tables


def forget_tables():
    with _tables_lock:
        _tables.clear()


def _upsert(code, table, facts):
    """INSERT OR REPLACE -> INSERT ... ON CONFLICT (primary key) DO UPDATE SET every other inserted column."""
    m = re.match(r'\s*INSERT\s+INTO\s+\w+\s*\(([^)]*)\)', code, re.I)
    columns = [c.strip() for c in m.group(1).split(',')] if m else facts['columns']
    pk = facts['pk']
    rest = [c for c in columns if c not in pk]
    action = 'DO UPDATE SET ' + ','.join(f'{c}=EXCLUDED.{c}' for c in rest) if rest else 'DO NOTHING'
    return code.rstrip().rstrip(';') + f' ON CONFLICT ({",".join(pk)}) {action}'


# ---- errors

def _as_sqlite_error(exc):
    message = (exc.diag.message_primary if getattr(exc, 'diag', None) else None) or str(exc)
    if isinstance(exc, psycopg.errors.IntegrityError) or exc.sqlstate == '23000':
        return sqlite3.IntegrityError(message)
    if isinstance(exc, (psycopg.errors.LockNotAvailable, psycopg.errors.QueryCanceled)):
        return sqlite3.OperationalError('database is locked')
    if isinstance(exc, psycopg.errors.UndefinedTable):
        return sqlite3.OperationalError('no such table: ' + message)
    return sqlite3.OperationalError(message)


# ---- connection

class Cursor:
    def __init__(self, cursor=None, lastrowid=None, rows=None):
        self._cursor, self.lastrowid, self._rows = cursor, lastrowid, rows

    @property
    def rowcount(self):
        return self._cursor.rowcount if self._cursor is not None else -1

    @property
    def description(self):
        return self._cursor.description if self._cursor is not None and self._rows is None else None

    def fetchone(self):
        if self._rows is not None:
            return self._rows.pop(0) if self._rows else None
        if self._cursor is None or self._cursor.description is None:
            return None
        return self._cursor.fetchone()

    def fetchall(self):
        if self._rows is not None:
            rows, self._rows = self._rows, []
            return rows
        if self._cursor is None or self._cursor.description is None:
            return []
        return self._cursor.fetchall()

    def fetchmany(self, size=1):
        return [r for r in (self.fetchone() for _ in range(size)) if r is not None]

    def __iter__(self):
        return iter(self.fetchall())


_WRITE = re.compile(r'\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b', re.I)
_BEGIN = re.compile(r'\s*BEGIN(\s+(IMMEDIATE|DEFERRED|EXCLUSIVE|TRANSACTION))?\s*;?\s*$', re.I)


class Connection:
    """A pooled PostgreSQL connection with the sqlite3.Connection interface the app uses."""

    def __init__(self, database):
        self._database = database
        self._conn = database.pool.getconn()
        self._locked = False
        self._savepoints = 0
        try:
            if database.schema:
                self._conn.execute(f'SET search_path TO "{database.schema}", public')
                self._conn.commit()   # SET is undone by a rollback otherwise
        except Exception:
            database.pool.putconn(self._conn)
            raise

    def _lock(self):
        if not self._locked:
            self._conn.execute('SELECT pg_advisory_xact_lock(%s)', (WRITE_LOCK,))
            self._locked = True

    def execute(self, sql, params=()):
        if _BEGIN.match(sql):
            self._lock()
            return Cursor()
        if re.match(r'\s*(COMMIT|END)\b', sql, re.I):
            self.commit()
            return Cursor()
        if re.match(r'\s*ROLLBACK\s*;?\s*$', sql, re.I):
            self.rollback()
            return Cursor()
        if re.match(r'\s*PRAGMA\b', sql, re.I):
            return Cursor()
        code, kind = translate(sql)
        write = bool(_WRITE.match(code))
        params = tuple(params) if not isinstance(params, dict) else params
        returning = False
        if kind != 'other':
            action, table = kind.split(':', 1)
            facts = _table_facts(self._conn).get(table)
            if action == 'replace' and facts:
                code = _upsert(code, table, facts)
            if facts and facts['identity'] and not re.search(r'\bRETURNING\b', code, re.I):
                code += ' RETURNING id'
                returning = True
        if write:
            self._lock()
            self._savepoints += 1
            name = f'krein_{self._savepoints}'
            self._conn.execute(f'SAVEPOINT {name}')
        cursor = self._conn.cursor(row_factory=_row_factory)
        try:
            cursor.execute(code, params, prepare=None)
        except psycopg.Error as exc:
            if write:
                self._conn.execute(f'ROLLBACK TO SAVEPOINT {name}')
            raise _as_sqlite_error(exc) from exc
        if write:
            self._conn.execute(f'RELEASE SAVEPOINT {name}')
        if returning:
            rows = cursor.fetchall()
            return Cursor(cursor, rows[-1][0] if rows else None, [])
        return Cursor(cursor)

    def executescript(self, script):
        self._lock()
        self._conn.execute(script)
        return Cursor()

    def commit(self):
        self._conn.commit()
        self._locked = False

    def rollback(self):
        self._conn.rollback()
        self._locked = False

    def close(self):
        if self._conn is None:
            return
        try:
            self._conn.rollback()   # like SQLite: closing without commit() discards the changes
        finally:
            self._database.pool.putconn(self._conn)
            self._conn = None

    @property
    def in_transaction(self):
        return self._conn is not None and self._conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


class Database:
    def __init__(self, url, schema=None):
        self.url, self.schema = url, schema
        self.pool = ConnectionPool(url, min_size=1, max_size=10, configure=_configure, open=True,
                                   kwargs={'autocommit': False, 'options': '-c lock_timeout=20s'})

    def connect(self):
        return Connection(self)


_databases = {}
_databases_lock = threading.Lock()


def database(url, schema=None):
    """One pool per database URL."""
    with _databases_lock:
        if url not in _databases:
            _databases[url] = Database(url)
        db = _databases[url]
    if schema == db.schema:
        return db
    view = Database.__new__(Database)   # the same pool, another schema (tests: one schema per test database)
    view.url, view.schema, view.pool = db.url, schema, db.pool
    return view


def create_schema(db):
    """Creates the tables in an empty database (or schema) and records that migrations up to the baseline are included."""
    from .migrations import POSTGRES_BASELINE
    admin = db.pool.getconn()
    try:
        admin.autocommit = True
        if db.schema:
            admin.execute(f'CREATE SCHEMA IF NOT EXISTS "{db.schema}"')
    finally:
        admin.autocommit = False
        db.pool.putconn(admin)
    with db.connect() as conn:
        conn._lock()
        exists = conn._conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='schema_migrations'").fetchone()
        if exists:
            return False
        conn._conn.execute(SCHEMA_FILE.read_text(encoding='utf-8'))
        conn._conn.execute('CREATE TABLE schema_migrations(version BIGINT PRIMARY KEY, applied_at TEXT NOT NULL)')
        from .db import now
        for version in range(2, POSTGRES_BASELINE + 1):
            conn._conn.execute('INSERT INTO schema_migrations VALUES(%s,%s)', (version, now()))
    return True

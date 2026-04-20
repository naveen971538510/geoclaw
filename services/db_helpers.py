"""
Shared DB helpers for GeoClaw agent state tables.

Routing:
  - DATABASE_URL set  → psycopg2 (Postgres); same tables, avoids APScheduler/SQLite locking
  - DATABASE_URL unset → sqlite3 (local dev fallback)

All callers use `?` placeholders; Postgres path transparently converts them to `%s`.
"""
import os
import re
import sqlite3
from pathlib import Path
from typing import Iterable, Sequence

from config import DB_PATH

# SQLite/Postgres identifier grammar: ASCII letter or underscore, then
# letters, digits, or underscores.  Identifiers can't be parametrised
# with ``?`` placeholders, so any code path that inlines a table or
# column name into an f-string MUST validate the name first — otherwise
# it's a latent SQL-injection footgun the moment a caller starts
# threading user or config input through.
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_identifier(value: str, kind: str = "identifier") -> str:
    """Validate ``value`` as a bare SQL identifier.

    Returns the identifier unchanged on success; raises ``ValueError``
    on anything that doesn't match the ``[A-Za-z_][A-Za-z0-9_]*`` grammar
    (so whitespace, quotes, statement terminators, dots, backticks, and
    every other non-ASCII character are rejected).

    ``kind`` is the noun printed in the error message (``"table"``,
    ``"column"``, etc.) so a caller can tell which argument was bad.
    """
    # ``fullmatch`` rather than ``match`` — ``$`` in Python's default
    # (non-MULTILINE) mode still accepts a trailing ``\n``, which would
    # let ``"foo\n; DROP TABLE x"`` slip through if the attacker could
    # also sneak the rest of the payload onto the same parameter.
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"Unsafe {kind} identifier: {value!r}")
    return value

PRAGMAS = (
    "PRAGMA journal_mode=WAL;",
    "PRAGMA foreign_keys=ON;",
    "PRAGMA cache_size=-8000;",
    "PRAGMA synchronous=NORMAL;",
)

_FORCE_SQLITE = os.environ.get("GEOCLAW_DB_BACKEND", "").strip().lower() in {"sqlite", "sqlite3", "local"}
_USE_POSTGRES = (not _FORCE_SQLITE) and bool((os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or "").strip())


def _pg_url() -> str:
    return (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or "").strip()


def _to_pg_sql(sql: str) -> str:
    """
    Convert SQLite-style ``?`` placeholders to Postgres ``%s``.

    Only replaces ``?`` that appear *outside* single-quoted string literals so
    that queries containing literal question marks (URLs, search text, LIKE
    patterns) are not corrupted.
    """
    out: list[str] = []
    in_quote = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_quote:
            in_quote = True
            out.append(ch)
        elif ch == "'" and in_quote:
            # Handle escaped single-quote ('')
            if i + 1 < len(sql) and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_quote = False
            out.append(ch)
        elif ch == "?" and not in_quote:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


class _PgConn:
    """Thin wrapper so callers can use sqlite-like connection methods on Postgres."""

    def __init__(self):
        import psycopg2
        self._conn = psycopg2.connect(_pg_url(), connect_timeout=10)
        self._conn.autocommit = False

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, _value):
        # Emulate sqlite3 row_factory assignment.
        pass

    def cursor(self):
        import psycopg2.extras
        return _PgCursor(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))

    def execute(self, sql: str, params: Sequence = ()):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executemany(self, sql: str, params_list):
        cur = self.cursor()
        cur.executemany(sql, params_list)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, _tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


class _PgCursor:
    def __init__(self, cur):
        self._cur = cur

    @property
    def lastrowid(self):
        return None

    @property
    def rowcount(self):
        return self._cur.rowcount

    def execute(self, sql: str, params: Sequence = ()):
        self._cur.execute(_to_pg_sql(sql), tuple(params or ()))
        return self

    def executemany(self, sql: str, params_list):
        self._cur.executemany(_to_pg_sql(sql), list(params_list or []))
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        rows = self._cur.fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self._cur.close()


def get_conn(db_path=None):
    if _USE_POSTGRES:
        return _PgConn()
    path = Path(db_path) if db_path else Path(DB_PATH)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    for pragma in PRAGMAS:
        cur.execute(pragma)
    return conn


def query(sql: str, params: Sequence = (), db_path=None):
    conn = get_conn(db_path=db_path)
    cur = conn.execute(sql, tuple(params or ()))
    rows = cur.fetchall()
    conn.close()
    return rows


def query_one(sql: str, params: Sequence = (), db_path=None):
    rows = query(sql, params=params, db_path=db_path)
    return rows[0] if rows else None


def execute(sql: str, params: Sequence = (), db_path=None):
    conn = get_conn(db_path=db_path)
    cur = conn.execute(sql, tuple(params or ()))
    conn.commit()
    lastrowid = getattr(cur, "lastrowid", None)
    rowcount = getattr(cur, "rowcount", 0)
    conn.close()
    return {"lastrowid": lastrowid, "rowcount": rowcount}


def executemany(sql: str, params_list: Iterable[Sequence], db_path=None):
    conn = get_conn(db_path=db_path)
    cur = conn.executemany(sql, list(params_list or []))
    conn.commit()
    rowcount = getattr(cur, "rowcount", 0)
    conn.close()
    return {"rowcount": rowcount}

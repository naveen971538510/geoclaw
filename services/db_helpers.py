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

PRAGMAS = (
    "PRAGMA journal_mode=WAL;",
    "PRAGMA foreign_keys=ON;",
    "PRAGMA cache_size=-8000;",
    "PRAGMA synchronous=NORMAL;",
)

_USE_POSTGRES = bool((os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or "").strip())


def _pg_url() -> str:
    return (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or "").strip()


def _to_pg_sql(sql: str) -> str:
    """Convert SQLite-style `?` placeholders to Postgres `%s`."""
    return re.sub(r"\?", "%s", sql)


class _PgConn:
    """Thin wrapper so callers can use `conn.execute()` / `conn.executemany()` like sqlite3."""

    def __init__(self):
        import psycopg2
        import psycopg2.extras
        self._conn = psycopg2.connect(_pg_url(), connect_timeout=10)
        self._conn.autocommit = False

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, _value):
        # Emulate sqlite3 row_factory assignment; we always use RealDictCursor for Postgres.
        pass

    def execute(self, sql: str, params: Sequence = ()):
        import psycopg2.extras
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(_to_pg_sql(sql), tuple(params or ()))
        return _PgCursor(cur)

    def executemany(self, sql: str, params_list):
        cur = self._conn.cursor()
        cur.executemany(_to_pg_sql(sql), list(params_list or []))
        return _PgCursor(cur)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


class _PgCursor:
    def __init__(self, cur):
        self._cur = cur

    @property
    def lastrowid(self):
        # Postgres doesn't have lastrowid; use RETURNING id where possible.
        # For now return None — callers that need lastrowid must use RETURNING.
        return None

    @property
    def rowcount(self):
        return self._cur.rowcount

    def fetchall(self):
        try:
            rows = self._cur.fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []


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

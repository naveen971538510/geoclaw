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


def get_conn(db_path=None):
    path = Path(db_path) if db_path else Path(DB_PATH)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    for pragma in PRAGMAS:
        cur.execute(pragma)
    return conn


def query(sql: str, params: Sequence = (), db_path=None):
    conn = get_conn(db_path=db_path)
    cur = conn.cursor()
    cur.execute(sql, tuple(params or ()))
    rows = cur.fetchall()
    conn.close()
    return rows


def query_one(sql: str, params: Sequence = (), db_path=None):
    rows = query(sql, params=params, db_path=db_path)
    return rows[0] if rows else None


def execute(sql: str, params: Sequence = (), db_path=None):
    conn = get_conn(db_path=db_path)
    cur = conn.cursor()
    cur.execute(sql, tuple(params or ()))
    conn.commit()
    lastrowid = cur.lastrowid
    rowcount = cur.rowcount
    conn.close()
    return {"lastrowid": lastrowid, "rowcount": rowcount}


def executemany(sql: str, params_list: Iterable[Sequence], db_path=None):
    conn = get_conn(db_path=db_path)
    cur = conn.cursor()
    cur.executemany(sql, list(params_list or []))
    conn.commit()
    rowcount = cur.rowcount
    conn.close()
    return {"rowcount": rowcount}

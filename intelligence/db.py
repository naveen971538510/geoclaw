"""
Postgres helpers for GeoClaw intelligence tables (macro_signals, geoclaw_signals, chart_signals).
Set DATABASE_URL (e.g. postgresql://user:pass@localhost:5432/geoclaw).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional

import psycopg2
import psycopg2.extras

try:
    import config  # noqa: F401 — load .env.geoclaw
except Exception:
    pass


def get_database_url() -> str:
    return (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or "").strip()


@contextmanager
def get_connection() -> Generator[Any, None, None]:
    url = get_database_url()
    if not url:
        raise RuntimeError("DATABASE_URL (or POSTGRES_URL) is not set")
    conn = psycopg2.connect(url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_intelligence_schema() -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS macro_signals (
                id SERIAL PRIMARY KEY,
                metric_name VARCHAR(128) NOT NULL,
                observed_at TIMESTAMPTZ NOT NULL,
                value DOUBLE PRECISION NOT NULL,
                previous_value DOUBLE PRECISION,
                pct_change DOUBLE PRECISION,
                extra JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (metric_name, observed_at)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS geoclaw_signals (
                id SERIAL PRIMARY KEY,
                signal_name VARCHAR(256) NOT NULL,
                value DOUBLE PRECISION,
                direction VARCHAR(16) NOT NULL,
                confidence DOUBLE PRECISION NOT NULL,
                explanation_plain_english TEXT NOT NULL,
                ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_geoclaw_signals_ts ON geoclaw_signals (ts DESC);
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chart_signals (
                id SERIAL PRIMARY KEY,
                ticker VARCHAR(32) NOT NULL,
                pattern_name VARCHAR(64) NOT NULL,
                direction VARCHAR(16) NOT NULL,
                confidence DOUBLE PRECISION NOT NULL,
                detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                bar_index INTEGER
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chart_signals_detected ON chart_signals (detected_at DESC);
            """
        )
        cur.close()


def query_all(sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params: Optional[tuple] = None) -> Optional[Dict[str, Any]]:
    rows = query_all(sql, params)
    return rows[0] if rows else None

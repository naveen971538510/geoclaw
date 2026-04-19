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
            ALTER TABLE geoclaw_signals
            ADD COLUMN IF NOT EXISTS signal_day DATE;
            """
        )
        cur.execute(
            """
            UPDATE geoclaw_signals
            SET signal_day = (ts AT TIME ZONE 'UTC')::date
            WHERE signal_day IS NULL;
            """
        )
        cur.execute(
            """
            DELETE FROM geoclaw_signals a
            USING geoclaw_signals b
            WHERE a.signal_name = b.signal_name
              AND a.direction = b.direction
              AND a.signal_day = b.signal_day
              AND (
                    a.ts < b.ts
                    OR (a.ts = b.ts AND a.id < b.id)
                  );
            """
        )
        cur.execute(
            """
            DROP INDEX IF EXISTS idx_geoclaw_signals_name_dir_day;
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_geoclaw_signals_name_dir_day
            ON geoclaw_signals (signal_name, direction, signal_day);
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
            CREATE TABLE IF NOT EXISTS price_data (
                id SERIAL PRIMARY KEY,
                ticker VARCHAR(20) NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_price_data_ticker_ts ON price_data (ticker, ts DESC);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chart_signals_detected ON chart_signals (detected_at DESC);
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS news_signals (
                id SERIAL PRIMARY KEY,
                headline TEXT NOT NULL,
                source VARCHAR(100) NOT NULL,
                url TEXT,
                sentiment VARCHAR(20) NOT NULL,
                confidence INT NOT NULL,
                reason TEXT,
                ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_news_signals_ts ON news_signals (ts DESC);
            """
        )
        # One-time dedupe safety for pre-existing macro_signals duplicates.
        cur.execute(
            """
            DELETE FROM macro_signals m
            USING macro_signals d
            WHERE m.metric_name = d.metric_name
              AND m.observed_at = d.observed_at
              AND m.id < d.id;
            """
        )
        # Ensure upsert target exists even on legacy DBs that were created before constraints.
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'macro_signals_metric_name_observed_at_key'
                ) THEN
                    ALTER TABLE macro_signals
                    ADD CONSTRAINT macro_signals_metric_name_observed_at_key
                    UNIQUE (metric_name, observed_at);
                END IF;
            END$$;
            """
        )
        # Agent memory snapshots — persists what the agent "believed" at each cycle
        # so restarts don't reset calibration and the backtester can correlate
        # accuracy improvements to specific memory states.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_memory_snapshots (
                id SERIAL PRIMARY KEY,
                run_id VARCHAR(32) NOT NULL,
                captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                win_rate_pct DOUBLE PRECISION,
                total_closed INTEGER,
                recent_errors JSONB DEFAULT '[]'::jsonb,
                prompt_suffix TEXT NOT NULL,
                UNIQUE (run_id)
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_memory_snapshots_captured
            ON agent_memory_snapshots (captured_at DESC);
            """
        )
        # Users table — multi-tenant foundation. email is the natural key.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email VARCHAR(320) NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name VARCHAR(128),
                role VARCHAR(32) NOT NULL DEFAULT 'user',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_login_at TIMESTAMPTZ
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_users_email ON users (lower(email));
            """
        )
        # Per-user LLM / API call accounting for quota enforcement.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_usage (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                endpoint VARCHAR(128) NOT NULL,
                called_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                cost_units INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_user_usage_user_time
            ON user_usage (user_id, called_at DESC);
            """
        )
        # Add user_id to principal tables — NULLable for backwards compat so
        # existing rows are treated as "system/shared" data visible to all users.
        # Backend jobs (scheduler, agent) leave user_id NULL.
        # User writes (portfolio, watchlist, custom theses) stamp user_id.
        _tenant_tables = (
            # System-owned, shared by default — user_id NULL for agent writes.
            "macro_signals", "geoclaw_signals", "chart_signals",
            "price_data", "news_signals", "agent_memory_snapshots",
            # User-owned: always should be scoped when written from user session.
            "agent_theses", "thesis_events", "agent_briefings",
            "thesis_confidence_log", "thesis_predictions",
            "portfolio_positions", "portfolio_snapshots", "watchlist",
        )
        for tbl in _tenant_tables:
            # Only add user_id if the table exists — some installs skip optional tables.
            cur.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s LIMIT 1;
                """,
                (tbl,),
            )
            if not cur.fetchone():
                continue
            cur.execute(
                f"""
                ALTER TABLE {tbl}
                ADD COLUMN IF NOT EXISTS user_id INTEGER
                    REFERENCES users(id) ON DELETE CASCADE;
                """
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{tbl}_user_id ON {tbl} (user_id);"
            )
        cur.close()


def save_memory_snapshot(
    run_id: str,
    win_rate_pct: Optional[float],
    total_closed: int,
    recent_errors: List[Dict[str, Any]],
    prompt_suffix: str,
) -> None:
    """Persist the calibration block the agent saw at this cycle."""
    import json as _json
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_memory_snapshots
                    (run_id, win_rate_pct, total_closed, recent_errors, prompt_suffix)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE
                    SET win_rate_pct  = EXCLUDED.win_rate_pct,
                        total_closed  = EXCLUDED.total_closed,
                        recent_errors = EXCLUDED.recent_errors,
                        prompt_suffix = EXCLUDED.prompt_suffix,
                        captured_at   = NOW();
                """,
                (run_id, win_rate_pct, total_closed, _json.dumps(recent_errors), prompt_suffix),
            )
            # Retain only the most recent 500 snapshots to prevent unbounded growth
            # (~17.5 k rows/year at 30-min cycles).
            cur.execute(
                """
                DELETE FROM agent_memory_snapshots
                WHERE id NOT IN (
                    SELECT id FROM agent_memory_snapshots
                    ORDER BY captured_at DESC
                    LIMIT 500
                );
                """
            )


def load_last_memory_snapshot() -> Optional[Dict[str, Any]]:
    """Return the most recent snapshot or None if the table is empty."""
    rows = query_all(
        "SELECT * FROM agent_memory_snapshots ORDER BY captured_at DESC LIMIT 1;"
    )
    return rows[0] if rows else None


def query_all(sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params: Optional[tuple] = None) -> Optional[Dict[str, Any]]:
    rows = query_all(sql, params)
    return rows[0] if rows else None

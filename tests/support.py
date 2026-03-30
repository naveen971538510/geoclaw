import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone


def iso_now(delta_hours=0):
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).isoformat()


def make_temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    bootstrap_test_db(path)
    return path


def bootstrap_test_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_theses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thesis_key TEXT UNIQUE,
            current_claim TEXT DEFAULT '',
            title TEXT DEFAULT '',
            confidence REAL DEFAULT 0.5,
            status TEXT DEFAULT 'active',
            last_update_reason TEXT DEFAULT '',
            terminal_risk TEXT DEFAULT '',
            watchlist_suggestion TEXT DEFAULT '',
            evidence_count INTEGER DEFAULT 0,
            confidence_velocity REAL DEFAULT 0.0,
            timeframe TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            last_updated_at TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ingested_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            headline TEXT,
            source_name TEXT,
            summary TEXT DEFAULT '',
            published_at TEXT DEFAULT '',
            fetched_at TEXT DEFAULT '',
            url TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT,
            thesis_key TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT '',
            payload_json TEXT DEFAULT '',
            audit_note TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contradictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thesis_key TEXT,
            explanation TEXT,
            severity TEXT,
            created_at TEXT DEFAULT '',
            resolved INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER DEFAULT 0,
            journal_type TEXT DEFAULT 'agent_loop',
            summary TEXT DEFAULT '',
            metrics_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_briefings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            briefing_text TEXT,
            generated_at TEXT,
            thesis_count INTEGER DEFAULT 0,
            contradiction_count INTEGER DEFAULT 0,
            chain_count INTEGER DEFAULT 0,
            action_count INTEGER DEFAULT 0,
            run_id INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS article_enrichment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER,
            signal TEXT DEFAULT '',
            sentiment_score REAL DEFAULT 0.0,
            impact_score INTEGER DEFAULT 0,
            confidence_score REAL DEFAULT 0.0,
            created_at TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS thesis_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thesis_key TEXT,
            predicted_direction TEXT,
            predicted_asset TEXT,
            symbol TEXT,
            price_at_prediction REAL,
            confidence_at_prediction REAL,
            predicted_at TEXT DEFAULT '',
            run_id INTEGER DEFAULT 0,
            check_after_hours INTEGER DEFAULT 24,
            checked_at TEXT DEFAULT '',
            price_at_check REAL,
            actual_change_pct REAL,
            outcome TEXT DEFAULT 'pending',
            outcome_note TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reasoning_chains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thesis_key TEXT,
            article_id INTEGER DEFAULT 0,
            chain_text TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sentiment_index_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            score REAL,
            label TEXT,
            components TEXT,
            recorded_at TEXT DEFAULT ''
        )
        """
    )
    conn.commit()
    conn.close()


def seed_sample_data(db_path):
    conn = sqlite3.connect(db_path)
    now = iso_now()
    theses = [
        (
            "Iran tensions affect Strait of Hormuz oil flow",
            "Iran tensions threaten oil shipping and lift crude risk premium.",
            "Hormuz Oil Risk",
            0.93,
            "confirmed",
            "Shipping risk premium rising on renewed Iran headlines.",
            "HIGH",
            "Monitor Brent crude, XLE, USO next.",
            5,
            0.08,
            "days",
            iso_now(-30),
            now,
        ),
        (
            "Gold safe-haven demand rises on missile strike fears",
            "Investors rotate into gold as conflict fears build.",
            "Gold Safety Bid",
            0.72,
            "active",
            "Safe-haven buying accelerated after missile headlines.",
            "MEDIUM",
            "Watch GC=F, GLD, ^VIX.",
            4,
            0.04,
            "days",
            iso_now(-25),
            now,
        ),
        (
            "China tariffs pressure semiconductor supply chains",
            "Tariff escalation could tighten chip supply and weigh on tech margins.",
            "China Tariff Pressure",
            0.66,
            "active",
            "Semiconductor names remain exposed to tariff escalation.",
            "MEDIUM",
            "Watch QQQ, SOXX, USDCNH=X.",
            3,
            0.02,
            "weeks",
            iso_now(-20),
            now,
        ),
        (
            "Ceasefire narrative losing momentum in Sudan",
            "De-escalation remains fragile and confidence is fading.",
            "Sudan Ceasefire Slips",
            0.28,
            "active",
            "Fresh clashes undercut the ceasefire narrative.",
            "LOW",
            "Watch regional risk headlines.",
            2,
            -0.05,
            "days",
            iso_now(-10),
            now,
        ),
    ]
    conn.executemany(
        """
        INSERT INTO agent_theses (
            thesis_key, current_claim, title, confidence, status, last_update_reason,
            terminal_risk, watchlist_suggestion, evidence_count, confidence_velocity,
            timeframe, created_at, last_updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        theses,
    )

    articles = [
        (
            "Iran tensions threaten Strait of Hormuz shipping lanes",
            "Reuters",
            "Oil traders are pricing a higher transport risk premium.",
            iso_now(-3),
            iso_now(-2),
            "https://example.com/iran-oil",
        ),
        (
            "Gold jumps as investors seek safety after missile strike",
            "Bloomberg",
            "Bullion gains as safe-haven demand strengthens.",
            iso_now(-4),
            iso_now(-3),
            "https://example.com/gold-safety",
        ),
        (
            "China tariffs raise new chip supply chain worries",
            "FT",
            "Tech manufacturers face renewed supply pressure.",
            iso_now(-5),
            iso_now(-4),
            "https://example.com/china-chips",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO ingested_articles (
            headline, source_name, summary, published_at, fetched_at, url
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        articles,
    )

    enrichments = [
        (1, "negative", -0.6, 88, 0.84, iso_now(-2)),
        (2, "positive", 0.5, 76, 0.80, iso_now(-3)),
        (3, "negative", -0.3, 71, 0.77, iso_now(-4)),
    ]
    conn.executemany(
        """
        INSERT INTO article_enrichment (
            article_id, signal, sentiment_score, impact_score, confidence_score, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        enrichments,
    )

    actions = [
        ("hedge", "Iran tensions affect Strait of Hormuz oil flow", "pending", iso_now(-1), '{"asset":"CL=F"}', "HIGH risk oil hedge proposal"),
        ("monitor", "China tariffs pressure semiconductor supply chains", "proposed", iso_now(-2), '{"asset":"QQQ"}', "Watch tariff headlines"),
    ]
    conn.executemany(
        """
        INSERT INTO agent_actions (
            action_type, thesis_key, status, created_at, payload_json, audit_note
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        actions,
    )

    conn.execute(
        """
        INSERT INTO contradictions (
            thesis_key, explanation, severity, created_at, resolved
        ) VALUES (?, ?, ?, ?, 0)
        """,
        (
            "China tariffs pressure semiconductor supply chains",
            "Supply easing headlines conflict with tariff escalation narrative.",
            "MEDIUM",
            iso_now(-1),
        ),
    )
    conn.execute(
        """
        INSERT INTO agent_journal (
            run_id, journal_type, summary, metrics_json, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (101, "agent_loop", "Baseline test journal entry", '{"chains":8}', iso_now(-1)),
    )
    conn.execute(
        """
        INSERT INTO agent_briefings (
            briefing_text, generated_at, thesis_count, contradiction_count, chain_count, action_count, run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "GeoClaw briefing text for test export and CLI coverage.",
            iso_now(-1),
            4,
            1,
            8,
            2,
            101,
        ),
    )
    conn.execute(
        """
        INSERT INTO reasoning_chains (
            thesis_key, article_id, chain_text
        ) VALUES (?, ?, ?)
        """,
        ("Iran tensions affect Strait of Hormuz oil flow", 1, "Article links to energy risk thesis."),
    )
    conn.commit()
    conn.close()


def remove_db(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass

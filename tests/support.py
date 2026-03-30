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
            source TEXT,
            summary TEXT DEFAULT '',
            published_at TEXT DEFAULT '',
            fetched_at TEXT DEFAULT '',
            sentiment_label TEXT DEFAULT '',
            relevance_score REAL DEFAULT 0.0,
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
            approval_state TEXT DEFAULT 'pending',
            reason TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            confidence REAL DEFAULT 0.0,
            evidence_count INTEGER DEFAULT 0,
            triggered_by TEXT DEFAULT 'test',
            created_at TEXT DEFAULT '',
            reviewed_at TEXT DEFAULT '',
            executed_at TEXT DEFAULT '',
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
            metrics TEXT DEFAULT '{}',
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
            run_id INTEGER DEFAULT 0,
            format TEXT DEFAULT 'trader'
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
            chain_text TEXT DEFAULT '',
            chain_json TEXT DEFAULT '[]',
            terminal_risk TEXT DEFAULT '',
            watchlist_suggestion TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS web_search_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            result_count INTEGER DEFAULT 0,
            searched_at TEXT DEFAULT '',
            triggered_by TEXT DEFAULT 'agent',
            thesis_key TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS web_sourced_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            headline TEXT,
            url TEXT UNIQUE,
            body TEXT,
            source TEXT,
            search_query TEXT,
            published_at TEXT,
            fetched_at TEXT DEFAULT '',
            is_reasoned INTEGER DEFAULT 0,
            thesis_key TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS learned_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            confidence_delta REAL NOT NULL,
            timeframe TEXT DEFAULT 'days',
            mechanism TEXT,
            market_implication TEXT,
            discovered_from TEXT,
            verification_count INTEGER DEFAULT 0,
            accuracy_pct REAL DEFAULT 0.0,
            created_at TEXT DEFAULT '',
            last_used TEXT DEFAULT '',
            last_verified TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            source TEXT DEFAULT 'learned'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_reliability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT UNIQUE,
            total_predictions INTEGER DEFAULT 0,
            verified_predictions INTEGER DEFAULT 0,
            refuted_predictions INTEGER DEFAULT 0,
            reliability_score REAL DEFAULT 0.65,
            last_updated TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            asset_type TEXT DEFAULT '',
            thesis_key TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            direction TEXT DEFAULT '',
            added_at TEXT DEFAULT '',
            status TEXT DEFAULT 'active'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT DEFAULT '',
            alert_type TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_type TEXT,
            subject TEXT DEFAULT '',
            content TEXT DEFAULT '',
            importance REAL DEFAULT 0.5,
            created_at TEXT DEFAULT '',
            last_recalled TEXT DEFAULT '',
            recall_count INTEGER DEFAULT 0,
            expired INTEGER DEFAULT 0,
            run_id INTEGER DEFAULT 0
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
            headline, source_name, source, summary, published_at, fetched_at, sentiment_label, relevance_score, url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                headline,
                source_name,
                source_name,
                summary,
                published_at,
                fetched_at,
                "negative" if "Iran" in headline or "tariffs" in headline else "positive",
                0.82 if "Iran" in headline else 0.74,
                url,
            )
            for headline, source_name, summary, published_at, fetched_at, url in articles
        ],
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
        (
            "hedge",
            "Iran tensions affect Strait of Hormuz oil flow",
            "pending",
            "pending",
            "HIGH risk oil hedge proposal",
            '{"asset":"CL=F"}',
            0.93,
            5,
            "test",
            iso_now(-1),
            "",
            "",
            '{"asset":"CL=F"}',
            "HIGH risk oil hedge proposal",
        ),
        (
            "monitor",
            "China tariffs pressure semiconductor supply chains",
            "draft",
            "draft",
            "Watch tariff headlines",
            '{"asset":"QQQ"}',
            0.66,
            3,
            "test",
            iso_now(-2),
            "",
            "",
            '{"asset":"QQQ"}',
            "Watch tariff headlines",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO agent_actions (
            action_type, thesis_key, status, approval_state, reason, metadata,
            confidence, evidence_count, triggered_by, created_at,
            reviewed_at, executed_at, payload_json, audit_note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            run_id, journal_type, summary, metrics_json, metrics, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            101,
            "agent_loop",
            "Baseline test journal entry",
            '{"chains":8,"run_goals":["Verify Hormuz thesis"],"active_research":{"searches_done":1,"articles_found":2,"articles_saved":1,"needs_found":1},"rule_learning":{"new_rules":0,"updated_rules":0},"actions_executed":{"auto":0,"manual":0},"autonomy_report_written":true}',
            '{"chains":8,"run_goals":["Verify Hormuz thesis"],"active_research":{"searches_done":1,"articles_found":2,"articles_saved":1,"needs_found":1},"rule_learning":{"new_rules":0,"updated_rules":0},"actions_executed":{"auto":0,"manual":0},"autonomy_report_written":true}',
            iso_now(-1),
        ),
    )
    conn.execute(
        """
        INSERT INTO agent_briefings (
            briefing_text, generated_at, thesis_count, contradiction_count, chain_count, action_count, run_id, format
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "GeoClaw briefing text for test export and CLI coverage.",
            iso_now(-1),
            4,
            1,
            8,
            2,
            101,
            "trader",
        ),
    )
    conn.execute(
        """
        INSERT INTO reasoning_chains (
            thesis_key, article_id, chain_text, chain_json, terminal_risk, watchlist_suggestion, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Iran tensions affect Strait of Hormuz oil flow",
            1,
            "Article links to energy risk thesis.",
            '[{"hop":1,"from":"headline","to":"oil","mechanism":"shipping risk","confidence":0.7,"timeframe":"days"}]',
            "Watch oil volatility",
            "CL=F",
            iso_now(-1),
        ),
    )
    conn.executemany(
        """
        INSERT INTO source_reliability (
            source_name, total_predictions, verified_predictions, refuted_predictions, reliability_score, last_updated
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("Reuters", 10, 8, 2, 0.88, iso_now(-1)),
            ("Bloomberg", 8, 6, 2, 0.84, iso_now(-1)),
            ("FT", 6, 4, 2, 0.79, iso_now(-1)),
        ],
    )
    conn.execute(
        """
        INSERT INTO alert_events (title, alert_type, created_at)
        VALUES (?, ?, ?)
        """,
        ("Oil risk premium widening", "market_alert", iso_now(-1)),
    )
    conn.execute(
        """
        INSERT INTO agent_memory (memory_type, subject, content, importance, created_at, run_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("pattern", "Iran tensions affect Strait of Hormuz oil flow", '{"confidence": 0.93, "status": "confirmed"}', 0.9, iso_now(-1), 101),
    )
    conn.commit()
    conn.close()


def remove_db(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass

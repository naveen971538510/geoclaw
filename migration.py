from services.db_helpers import get_conn


def _table_columns(cur, table_name: str):
    cur.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cur.fetchall()}


def _ensure_column(cur, table_name: str, column_name: str, ddl: str):
    if column_name not in _table_columns(cur, table_name):
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")


def run_migration(verbose: bool = False):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ingested_articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_name TEXT NOT NULL,
        external_id TEXT,
        headline TEXT NOT NULL,
        summary TEXT,
        url TEXT NOT NULL,
        published_at TEXT,
        language TEXT,
        country TEXT,
        fetched_at TEXT,
        content_hash TEXT,
        is_duplicate INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_ingested_articles_url
    ON ingested_articles(url)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_ingested_articles_published_at
    ON ingested_articles(published_at)
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_ingested_articles_fetched_at
    ON ingested_articles(fetched_at)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_ingested_articles_content_hash
    ON ingested_articles(content_hash)
    """)
    _ensure_column(cur, "ingested_articles", "is_reasoned", "INTEGER DEFAULT 0")
    _ensure_column(cur, "ingested_articles", "entity_tags", "TEXT DEFAULT '[]'")
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_ingested_articles_reasoned_fetched
    ON ingested_articles(is_reasoned, fetched_at)
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS article_enrichment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id INTEGER NOT NULL,
        signal TEXT,
        sentiment_score REAL DEFAULT 0,
        impact_score INTEGER DEFAULT 0,
        asset_tags TEXT,
        macro_tags TEXT,
        watchlist_hits TEXT,
        alert_tags TEXT,
        thesis TEXT,
        bull_case TEXT,
        bear_case TEXT,
        what_to_watch TEXT,
        confidence INTEGER DEFAULT 0,
        created_at TEXT,
        FOREIGN KEY(article_id) REFERENCES ingested_articles(id)
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_article_enrichment_article_id
    ON article_enrichment(article_id)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_article_enrichment_impact_score
    ON article_enrichment(impact_score)
    """)
    _ensure_column(cur, "article_enrichment", "why_it_matters", "TEXT")
    _ensure_column(cur, "article_enrichment", "confidence_score", "REAL DEFAULT 0.5")
    _ensure_column(cur, "article_enrichment", "urgency_level", "TEXT DEFAULT 'medium'")
    _ensure_column(cur, "article_enrichment", "impact_radius", "TEXT DEFAULT 'regional'")
    _ensure_column(cur, "article_enrichment", "contradicts_narrative", "INTEGER DEFAULT 0")
    _ensure_column(cur, "article_enrichment", "llm_category", "TEXT DEFAULT 'other'")
    _ensure_column(cur, "article_enrichment", "llm_importance", "TEXT DEFAULT 'medium'")
    _ensure_column(cur, "article_enrichment", "llm_mode", "TEXT DEFAULT ''")
    _ensure_column(cur, "article_enrichment", "llm_fallback_reason", "TEXT DEFAULT ''")
    _ensure_column(cur, "article_enrichment", "cluster_key", "TEXT DEFAULT ''")
    _ensure_column(cur, "article_enrichment", "cluster_size", "INTEGER DEFAULT 1")
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_article_enrichment_cluster_key
    ON article_enrichment(cluster_key)
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS market_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        label TEXT NOT NULL,
        price REAL,
        change_abs REAL,
        change_pct REAL,
        asof TEXT
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_market_snapshots_symbol_asof
    ON market_snapshots(symbol, asof)
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS alert_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id INTEGER NOT NULL,
        priority TEXT,
        reason TEXT,
        created_at TEXT,
        is_read INTEGER DEFAULT 0,
        FOREIGN KEY(article_id) REFERENCES ingested_articles(id)
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_alert_events_article_id
    ON alert_events(article_id)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_alert_events_created_at
    ON alert_events(created_at)
    """)
    _ensure_column(cur, "alert_events", "is_starred", "INTEGER DEFAULT 0")
    _ensure_column(cur, "alert_events", "status", "TEXT DEFAULT 'open'")
    _ensure_column(cur, "alert_events", "resolved", "INTEGER DEFAULT 0")
    _ensure_column(cur, "alert_events", "resolution_note", "TEXT DEFAULT ''")
    _ensure_column(cur, "alert_events", "resolved_at", "TEXT DEFAULT ''")
    _ensure_column(cur, "alert_events", "alert_type", "TEXT DEFAULT ''")
    _ensure_column(cur, "alert_events", "title", "TEXT DEFAULT ''")
    _ensure_column(cur, "alert_events", "body", "TEXT DEFAULT ''")
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_alert_events_status_resolved
    ON alert_events(status, resolved, created_at)
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_alert_events_type
    ON alert_events(alert_type, created_at DESC)
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS agent_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_type TEXT,
        started_at TEXT,
        finished_at TEXT,
        status TEXT,
        items_fetched INTEGER DEFAULT 0,
        items_kept INTEGER DEFAULT 0,
        alerts_created INTEGER DEFAULT 0,
        error_text TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS agent_goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        priority INTEGER DEFAULT 50,
        watch_targets_json TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_goals_active_priority
    ON agent_goals(is_active, priority DESC)
    """)
    _ensure_column(cur, "agent_goals", "source", "TEXT DEFAULT 'manual'")
    _ensure_column(cur, "agent_goals", "status", "TEXT DEFAULT 'active'")
    _ensure_column(cur, "agent_goals", "thesis_key", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_goals", "success_criteria", "TEXT DEFAULT ''")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS agent_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id INTEGER,
        memory_type TEXT,
        thesis TEXT,
        confidence INTEGER DEFAULT 0,
        status TEXT,
        notes TEXT,
        linked_decision_id INTEGER,
        created_at TEXT,
        updated_at TEXT,
        FOREIGN KEY(article_id) REFERENCES ingested_articles(id)
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_memory_article_type
    ON agent_memory(article_id, memory_type, updated_at)
    """)
    _ensure_column(cur, "agent_memory", "thesis_key", "TEXT DEFAULT ''")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS agent_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id INTEGER,
        decision_type TEXT,
        reason TEXT,
        confidence INTEGER DEFAULT 0,
        priority_score INTEGER DEFAULT 0,
        state TEXT,
        created_at TEXT,
        FOREIGN KEY(article_id) REFERENCES ingested_articles(id)
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_decisions_created_at
    ON agent_decisions(created_at)
    """)
    _ensure_column(cur, "agent_decisions", "cluster_key", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_decisions", "thesis_key", "TEXT DEFAULT ''")
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_decisions_thesis_state
    ON agent_decisions(thesis_key, state, created_at)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS agent_theses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thesis_key TEXT NOT NULL UNIQUE,
        current_claim TEXT,
        confidence REAL DEFAULT 0.5,
        status TEXT DEFAULT 'active',
        last_updated_at TEXT,
        evidence_count INTEGER DEFAULT 0,
        created_at TEXT,
        last_article_id INTEGER,
        last_decision_id INTEGER,
        contradiction_count INTEGER DEFAULT 0,
        notes TEXT
    )
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_theses_status_updated
    ON agent_theses(status, last_updated_at)
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_theses_status_confidence
    ON agent_theses(status, confidence DESC, last_updated_at)
    """)
    _ensure_column(cur, "agent_theses", "last_update_reason", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_theses", "title", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_theses", "bull_case", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_theses", "bear_case", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_theses", "key_risk", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_theses", "watch_for_next", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_theses", "category", "TEXT DEFAULT 'other'")
    _ensure_column(cur, "agent_theses", "terminal_risk", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_theses", "watchlist_suggestion", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_theses", "timeframe", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_theses", "confidence_velocity", "REAL DEFAULT 0.0")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS agent_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_type TEXT,
        title TEXT,
        details TEXT,
        related_article_id INTEGER,
        status TEXT,
        due_hint TEXT,
        created_at TEXT,
        updated_at TEXT,
        FOREIGN KEY(related_article_id) REFERENCES ingested_articles(id)
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_tasks_status_updated
    ON agent_tasks(status, updated_at)
    """)
    _ensure_column(cur, "agent_tasks", "source_count", "INTEGER DEFAULT 1")
    _ensure_column(cur, "agent_tasks", "confidence_score", "REAL DEFAULT 0.5")
    _ensure_column(cur, "agent_tasks", "urgency_level", "TEXT DEFAULT 'medium'")
    _ensure_column(cur, "agent_tasks", "impact_radius", "TEXT DEFAULT 'regional'")
    _ensure_column(cur, "agent_tasks", "ttl", "TEXT")
    _ensure_column(cur, "agent_tasks", "identity_key", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_tasks", "closed_reason", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_tasks", "thesis_key", "TEXT DEFAULT ''")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS llm_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cache_key TEXT NOT NULL UNIQUE,
        mode TEXT NOT NULL,
        analysis_json TEXT,
        fallback_reason TEXT,
        created_at TEXT,
        expires_at TEXT
    )
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_llm_cache_mode_expires
    ON llm_cache(mode, expires_at)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS llm_usage_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cache_key TEXT,
        mode TEXT,
        outcome TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_llm_usage_created
    ON llm_usage_log(created_at)
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_llm_usage_mode_created
    ON llm_usage_log(mode, created_at)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS llm_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model TEXT,
        tokens INTEGER DEFAULT 0,
        context_snippet TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        run_id INTEGER
    )
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_llm_usage_created
    ON llm_usage(created_at DESC)
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS agent_journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER,
        journal_type TEXT,
        summary TEXT,
        metrics_json TEXT,
        created_at TEXT,
        FOREIGN KEY(run_id) REFERENCES agent_runs(id)
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_journal_run_created
    ON agent_journal(run_id, created_at)
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_journal_type_created
    ON agent_journal(journal_type, created_at)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS agent_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action_type TEXT NOT NULL,
        payload_json TEXT,
        thesis_key TEXT,
        confidence REAL DEFAULT 0.5,
        evidence_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'proposed',
        triggered_by TEXT,
        created_at TEXT,
        reviewed_at TEXT,
        executed_at TEXT,
        audit_note TEXT
    )
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_actions_status_created
    ON agent_actions(status, created_at)
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_actions_thesis_status
    ON agent_actions(thesis_key, status, created_at)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS thesis_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thesis_key TEXT,
        event_type TEXT,
        note TEXT,
        confidence_at_event REAL,
        evidence_count_at_event INTEGER,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_thesis_events_key_created
    ON thesis_events(thesis_key, created_at)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS agent_lessons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        decision_id INTEGER,
        thesis_key TEXT,
        verdict TEXT,
        lesson TEXT,
        confidence_delta REAL,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_lessons_thesis_created
    ON agent_lessons(thesis_key, created_at)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS agent_calibration (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_name TEXT,
        category TEXT,
        over_confident INTEGER DEFAULT 0,
        count INTEGER DEFAULT 0,
        created_at TEXT,
        thesis_key TEXT,
        decision_id INTEGER,
        verdict TEXT,
        lesson TEXT,
        confidence_delta REAL DEFAULT 0.0,
        predicted_confidence REAL DEFAULT 0.0,
        predicted_direction TEXT,
        actual_outcome TEXT,
        row_type TEXT DEFAULT 'reflection'
    )
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_calibration_source_category
    ON agent_calibration(source_name, category, created_at)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reasoning_chains (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id INTEGER,
        thesis_key TEXT,
        chain_json TEXT,
        terminal_risk TEXT,
        watchlist_suggestion TEXT,
        created_at TEXT
    )
    """)
    _ensure_column(cur, "reasoning_chains", "reasoning_source", "TEXT DEFAULT 'rule_engine'")
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_reasoning_chains_created
    ON reasoning_chains(created_at)
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_reasoning_chains_thesis_article
    ON reasoning_chains(thesis_key, article_id, created_at)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS agent_briefings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        briefing_text TEXT,
        generated_at TEXT,
        thesis_count INTEGER DEFAULT 0,
        contradiction_count INTEGER DEFAULT 0,
        chain_count INTEGER DEFAULT 0,
        action_count INTEGER DEFAULT 0
    )
    """)
    _ensure_column(cur, "agent_briefings", "run_id", "INTEGER")
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_briefings_generated
    ON agent_briefings(generated_at)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS price_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        name TEXT,
        category TEXT,
        price REAL,
        change_pct REAL,
        direction TEXT,
        captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_price_snapshots_sym_time
    ON price_snapshots(symbol, captured_at DESC)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS contradictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thesis_key TEXT,
        article_headline_a TEXT,
        article_headline_b TEXT,
        explanation TEXT,
        severity TEXT,
        created_at TEXT,
        resolved INTEGER DEFAULT 0,
        resolution_note TEXT DEFAULT '',
        resolved_at TEXT DEFAULT ''
    )
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_contradictions_created
    ON contradictions(created_at DESC)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS thesis_confidence_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thesis_key TEXT,
        confidence REAL,
        run_id INTEGER DEFAULT 0,
        recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_conf_log_thesis_time
    ON thesis_confidence_log(thesis_key, recorded_at DESC)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS thesis_predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thesis_key TEXT,
        predicted_direction TEXT,
        predicted_asset TEXT,
        symbol TEXT,
        price_at_prediction REAL,
        confidence_at_prediction REAL,
        predicted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        run_id INTEGER,
        check_after_hours INTEGER DEFAULT 24,
        checked_at TIMESTAMP,
        price_at_check REAL,
        actual_change_pct REAL,
        outcome TEXT DEFAULT 'pending',
        outcome_note TEXT
    )
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_predictions_thesis
    ON thesis_predictions(thesis_key, outcome)
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_predictions_pending
    ON thesis_predictions(outcome, predicted_at)
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS source_reliability (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_name TEXT UNIQUE,
        total_predictions INTEGER DEFAULT 0,
        verified_predictions INTEGER DEFAULT 0,
        refuted_predictions INTEGER DEFAULT 0,
        reliability_score REAL DEFAULT 0.65,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cur.execute("""
    INSERT OR IGNORE INTO source_reliability (source_name, reliability_score) VALUES
        ('Reuters', 0.90),
        ('Bloomberg', 0.90),
        ('FT', 0.85),
        ('BBC', 0.80),
        ('CNBC', 0.75),
        ('Al Jazeera', 0.78),
        ('MarketWatch', 0.72),
        ('Oil Price', 0.68),
        ('Fed Reserve', 0.95),
        ('ECB', 0.95),
        ('IMF', 0.92)
    """)

    conn.commit()

    if verbose:
        for table in [
            "ingested_articles",
            "article_enrichment",
            "market_snapshots",
            "alert_events",
            "agent_runs",
            "agent_goals",
            "agent_memory",
            "agent_decisions",
            "agent_theses",
            "agent_tasks",
            "agent_journal",
            "agent_actions",
            "thesis_events",
            "agent_lessons",
            "agent_calibration",
            "reasoning_chains",
            "agent_briefings",
            "llm_cache",
            "llm_usage_log",
        ]:
            cur.execute("SELECT COUNT(*) FROM " + table)
            count = cur.fetchone()[0]
            print(f"{table}: OK ({count} rows)")

    conn.close()
    if verbose:
        print("STEP 1 migration complete")


if __name__ == "__main__":
    run_migration(verbose=True)

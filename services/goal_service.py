import json
from datetime import datetime, timezone
from typing import Dict, List

from config import DB_PATH
from services.db_helpers import get_conn as shared_get_conn


DEFAULT_AGENT_GOALS = [
    {
        "name": "Protect capital around macro shocks",
        "description": "Track high-impact macro and geopolitical shifts that can quickly change market direction.",
        "priority": 95,
        "watch_targets": ["oil", "rates", "inflation", "sanctions", "war"],
    },
    {
        "name": "Monitor FX pressure points",
        "description": "Watch GBP, USD, EUR, and JPY stories that can alter currency positioning.",
        "priority": 85,
        "watch_targets": ["gbp", "usd", "eur", "yen", "forex"],
    },
    {
        "name": "Track commodity follow-through",
        "description": "Watch whether oil and gold stories are being confirmed, contradicted, or fading.",
        "priority": 80,
        "watch_targets": ["oil", "gold", "opec", "bullion"],
    },
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn():
    return shared_get_conn(DB_PATH)


def _json(value) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def _loads(value) -> List[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
        if isinstance(data, list):
            return [str(x).strip().lower() for x in data if str(x).strip()]
    except Exception:
        pass
    return []


def _table_columns(cur, table_name: str):
    cur.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cur.fetchall()}


def _ensure_column(cur, table_name: str, column_name: str, ddl: str):
    if column_name not in _table_columns(cur, table_name):
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")


def ensure_agent_tables():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
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
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_goals_active_priority
        ON agent_goals(is_active, priority DESC)
        """
    )
    _ensure_column(cur, "agent_goals", "source", "TEXT DEFAULT 'manual'")
    _ensure_column(cur, "agent_goals", "status", "TEXT DEFAULT 'active'")
    _ensure_column(cur, "agent_goals", "thesis_key", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_goals", "success_criteria", "TEXT DEFAULT ''")
    cur.execute(
        """
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
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_memory_article_type
        ON agent_memory(article_id, memory_type, updated_at)
        """
    )
    _ensure_column(cur, "agent_memory", "thesis_key", "TEXT DEFAULT ''")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER,
            decision_type TEXT,
            reason TEXT,
            confidence INTEGER DEFAULT 0,
            priority_score INTEGER DEFAULT 0,
            state TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_decisions_created_at
        ON agent_decisions(created_at)
        """
    )
    _ensure_column(cur, "agent_decisions", "cluster_key", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_decisions", "thesis_key", "TEXT DEFAULT ''")
    cur.execute(
        """
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
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_theses_status_updated
        ON agent_theses(status, last_updated_at)
        """
    )
    _ensure_column(cur, "agent_theses", "last_update_reason", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_theses", "title", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_theses", "bull_case", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_theses", "bear_case", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_theses", "key_risk", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_theses", "watch_for_next", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_theses", "category", "TEXT DEFAULT 'other'")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT,
            title TEXT,
            details TEXT,
            related_article_id INTEGER,
            status TEXT,
            due_hint TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_tasks_status_updated
        ON agent_tasks(status, updated_at)
        """
    )
    _ensure_column(cur, "agent_tasks", "source_count", "INTEGER DEFAULT 1")
    _ensure_column(cur, "agent_tasks", "confidence_score", "REAL DEFAULT 0.5")
    _ensure_column(cur, "agent_tasks", "urgency_level", "TEXT DEFAULT 'medium'")
    _ensure_column(cur, "agent_tasks", "impact_radius", "TEXT DEFAULT 'regional'")
    _ensure_column(cur, "agent_tasks", "ttl", "TEXT")
    _ensure_column(cur, "agent_tasks", "identity_key", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_tasks", "closed_reason", "TEXT DEFAULT ''")
    _ensure_column(cur, "agent_tasks", "thesis_key", "TEXT DEFAULT ''")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key TEXT NOT NULL UNIQUE,
            mode TEXT NOT NULL,
            analysis_json TEXT,
            fallback_reason TEXT,
            created_at TEXT,
            expires_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_llm_cache_mode_expires
        ON llm_cache(mode, expires_at)
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key TEXT,
            mode TEXT,
            outcome TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_llm_usage_created
        ON llm_usage_log(created_at)
        """
    )
    cur.execute(
        """
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
            created_at TEXT
        )
        """
    )
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
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            journal_type TEXT,
            summary TEXT,
            metrics_json TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_journal_run_created
        ON agent_journal(run_id, created_at)
        """
    )
    cur.execute(
        """
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
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_actions_status_created
        ON agent_actions(status, created_at)
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS thesis_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thesis_key TEXT,
            event_type TEXT,
            note TEXT,
            confidence_at_event REAL,
            evidence_count_at_event INTEGER,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_thesis_events_key_created
        ON thesis_events(thesis_key, created_at)
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id INTEGER,
            thesis_key TEXT,
            verdict TEXT,
            lesson TEXT,
            confidence_delta REAL,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
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
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_calibration_source_category
        ON agent_calibration(source_name, category, created_at)
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reasoning_chains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER,
            thesis_key TEXT,
            chain_json TEXT,
            terminal_risk TEXT,
            watchlist_suggestion TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_briefings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            briefing_text TEXT,
            generated_at TEXT,
            thesis_count INTEGER DEFAULT 0,
            contradiction_count INTEGER DEFAULT 0,
            chain_count INTEGER DEFAULT 0,
            action_count INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


def _seed_default_goals_if_empty():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM agent_goals")
    count = int(cur.fetchone()[0] or 0)
    if count == 0:
        now = utc_now_iso()
        for goal in DEFAULT_AGENT_GOALS:
            cur.execute(
                """
                INSERT INTO agent_goals (name, description, priority, watch_targets_json, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    goal["name"],
                    goal.get("description", ""),
                    int(goal.get("priority", 50)),
                    _json(goal.get("watch_targets", [])),
                    now,
                    now,
                ),
            )
        conn.commit()
    conn.close()


def list_goals(active_only: bool = False) -> List[Dict]:
    ensure_agent_tables()
    _seed_default_goals_if_empty()
    conn = get_conn()
    cur = conn.cursor()
    if active_only:
        cur.execute(
            """
            SELECT id, name, description, priority, watch_targets_json, is_active, created_at, updated_at,
                   source, status, thesis_key, success_criteria
            FROM agent_goals
            WHERE is_active = 1
            ORDER BY priority DESC, id DESC
            """
        )
    else:
        cur.execute(
            """
            SELECT id, name, description, priority, watch_targets_json, is_active, created_at, updated_at,
                   source, status, thesis_key, success_criteria
            FROM agent_goals
            ORDER BY is_active DESC, priority DESC, id DESC
            """
        )
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": int(row["id"]),
            "name": row["name"],
            "description": row["description"] or "",
            "priority": int(row["priority"] or 0),
            "watch_targets": _loads(row["watch_targets_json"]),
            "is_active": bool(row["is_active"]),
            "source": row["source"] or "manual",
            "status": row["status"] or ("active" if row["is_active"] else "inactive"),
            "thesis_key": row["thesis_key"] or "",
            "success_criteria": row["success_criteria"] or "",
            "created_at": row["created_at"] or "",
            "updated_at": row["updated_at"] or "",
        }
        for row in rows
    ]


def create_goal(
    name: str,
    description: str = "",
    priority: int = 50,
    watch_targets=None,
    is_active: bool = True,
    source: str = "manual",
    status: str = "active",
    thesis_key: str = "",
    success_criteria: str = "",
) -> Dict:
    ensure_agent_tables()
    _seed_default_goals_if_empty()
    now = utc_now_iso()
    targets = _loads(_json(watch_targets or []))
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO agent_goals (
            name, description, priority, watch_targets_json, is_active,
            created_at, updated_at, source, status, thesis_key, success_criteria
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(name or "").strip() or "Untitled goal",
            str(description or ""),
            int(priority or 50),
            _json(targets),
            1 if is_active else 0,
            now,
            now,
            str(source or "manual"),
            str(status or ("active" if is_active else "inactive")),
            str(thesis_key or ""),
            str(success_criteria or ""),
        ),
    )
    goal_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return [g for g in list_goals(active_only=False) if g["id"] == goal_id][0]


def generate_autonomous_goals(db=None, limit_new: int = 3) -> List[Dict]:
    ensure_agent_tables()
    from services.llm_service import analyse_custom_json

    conn = db or get_conn()
    close_conn = db is None
    cur = conn.cursor()
    cur.execute(
        """
        SELECT thesis_key, title, current_claim, confidence, category
        FROM agent_theses
        WHERE COALESCE(status, 'active') IN ('active', 'tracking')
        ORDER BY confidence DESC, evidence_count DESC, id DESC
        LIMIT 8
        """
    )
    theses = [dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT decision_type, reason, thesis_key
        FROM agent_decisions
        ORDER BY created_at DESC, id DESC
        LIMIT 10
        """
    )
    decisions = [dict(row) for row in cur.fetchall()]

    fallback_goals = {
        "goals": [
            {
                "title": f"Investigate {(item.get('title') or item.get('current_claim') or item.get('thesis_key') or 'active thesis')[:48]}",
                "description": f"Gather confirming or contradicting evidence for {(item.get('current_claim') or item.get('title') or 'this thesis')}.",
                "priority": "high" if idx == 0 else "medium",
                "thesis_key": item.get("thesis_key", ""),
                "success_criteria": "At least three corroborating or contradicting evidence points are recorded.",
            }
            for idx, item in enumerate(theses[:3])
        ] or [
            {
                "title": "Investigate active thesis set",
                "description": "Gather more confirming and contradicting evidence across the active theses.",
                "priority": "medium",
                "thesis_key": "",
                "success_criteria": "Three new evidence points are added.",
            }
        ]
    }

    system_text = (
        "You are an intelligence analyst agent. Based on these active theses and recent decisions, "
        "generate 3 new investigation goals that would most improve understanding of the current situation. "
        "Return JSON only: { goals: [{ title, description, priority, thesis_key, success_criteria }] }"
    )
    user_text = (
        f"Active theses: {json.dumps(theses, ensure_ascii=False)}\n"
        f"Recent decisions: {json.dumps(decisions, ensure_ascii=False)}"
    )

    def _valid(payload):
        return isinstance(payload, dict) and isinstance(payload.get("goals"), list)

    def _clean(payload):
        rows = []
        for item in (payload.get("goals") or [])[:3]:
            rows.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "description": str(item.get("description") or "").strip(),
                    "priority": str(item.get("priority") or "medium").strip().lower(),
                    "thesis_key": str(item.get("thesis_key") or "").strip(),
                    "success_criteria": str(item.get("success_criteria") or "").strip(),
                }
            )
        return {"goals": rows}

    analysis = analyse_custom_json(
        system_text,
        user_text,
        fallback=fallback_goals,
        mode="autonomous_goals",
        cache_key="autonomous_goals::" + "|".join([str(item.get("thesis_key") or "") for item in theses[:5]]),
        validator=_valid,
        cleaner=_clean,
    )["analysis"]

    created = []
    priority_map = {"high": 90, "medium": 65, "low": 40}
    remaining = max(0, int(limit_new or 0))
    for item in analysis.get("goals", [])[:3]:
        if remaining <= 0:
            break
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        cur.execute("SELECT id FROM agent_goals WHERE LOWER(name) = LOWER(?) LIMIT 1", (title,))
        if cur.fetchone():
            continue
        created.append(
            create_goal(
                name=title,
                description=item.get("description", ""),
                priority=priority_map.get(str(item.get("priority") or "medium").lower(), 65),
                watch_targets=[item.get("thesis_key", "")] if item.get("thesis_key") else [],
                is_active=True,
                source="autonomous",
                status="active",
                thesis_key=item.get("thesis_key", ""),
                success_criteria=item.get("success_criteria", ""),
            )
        )
        remaining -= 1
    if close_conn:
        conn.close()
    return created

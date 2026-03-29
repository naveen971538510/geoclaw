# geoclaw_terminal_routes.py
# -------------------------------------------------------
# Sprint: Agent Visibility – new /terminal/* route handlers
# -------------------------------------------------------
# HOW TO INTEGRATE:
#   Option A (Blueprint): register this blueprint in app.py
#       from geoclaw_terminal_routes import terminal_bp
#       app.register_blueprint(terminal_bp)
#   Option B (inline): copy each function + route decorator
#       directly into app.py before `if __name__ == "__main__"`
# -------------------------------------------------------

from flask import Blueprint, jsonify, current_app
import datetime, copy

terminal_bp = Blueprint("terminal_vis", __name__)

# ── helpers ──────────────────────────────────────────────────────────────────

def _db():
    """Return the db object however your app exposes it.
    Adjust if you use SQLAlchemy, raw sqlite3, etc."""
    from app import db          # <- change 'app' to your actual module name
    return db


def _query(sql, params=()):
    """Run a SELECT and return list-of-dicts."""
    import sqlite3
    db_path = current_app.config.get("DATABASE", "geoclaw.db")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def _query_one(sql, params=()):
    rows = _query(sql, params)
    return rows[0] if rows else None


def _safe_json(raw):
    """Parse a JSON column that may already be a dict."""
    import json
    if raw is None:
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}


# ── 1. Agent Summary ─────────────────────────────────────────────────────────

@terminal_bp.route("/terminal/agent-summary")
def agent_summary():
    """
    Returns plain-English summary of the latest agent_loop journal entry.
    Reads from agent_journal (or journal_entries – adjust table name below).
    """
    import json

    # ── fetch last two runs so we can diff ──────────────────────────────────
    # Adjust table name / column names to match your schema
    rows = _query(
        """
        SELECT id, run_id, journal_type, summary, created_at, metrics
        FROM   agent_journal
        WHERE  journal_type = 'agent_loop'
        ORDER  BY id DESC
        LIMIT  2
        """,
    )

    if not rows:
        return jsonify({"status": "ok", "summary": None, "message": "No agent runs yet."})

    latest = rows[0]
    prev   = rows[1] if len(rows) > 1 else None

    m  = _safe_json(latest.get("metrics", {}))
    pm = _safe_json(prev.get("metrics", {})) if prev else {}

    # ── thesis updates ───────────────────────────────────────────────────────
    tu      = m.get("thesis_updates", {})
    touched = tu.get("touched", [])

    # ── task closures ────────────────────────────────────────────────────────
    tc         = m.get("task_closures", {})
    tasks_closed = sum([
        tc.get("stale", 0),
        tc.get("contradicted", 0),
        tc.get("superseded", 0),
        tc.get("completed", 0),
    ])

    # ── cluster count ────────────────────────────────────────────────────────
    clusters_seen = m.get("cluster_identities_seen", 0)

    # ── articles ─────────────────────────────────────────────────────────────
    stories_reviewed = m.get("items_kept", m.get("items_fetched", 0))

    # ── actions ──────────────────────────────────────────────────────────────
    actions_proposed = m.get("action_proposals_created", 0)

    # ── top belief change ────────────────────────────────────────────────────
    conf_updates      = tu.get("confidence_updates", 0)
    top_belief_change = touched[0] if touched else "No thesis touched."
    reason_for_change = latest.get("summary", "")

    # ── previous-run deltas ──────────────────────────────────────────────────
    prev_upserts = pm.get("thesis_updates", {}).get("upserts", 0) if pm else 0
    upsert_delta = tu.get("upserts", 0) - prev_upserts

    summary = {
        "run_id"            : latest.get("run_id"),
        "run_at"            : latest.get("created_at"),
        "stories_reviewed"  : stories_reviewed,
        "clusters_reviewed" : clusters_seen,
        "theses_updated"    : tu.get("upserts", 0),
        "theses_updated_delta": upsert_delta,
        "confidence_updates": conf_updates,
        "tasks_closed"      : tasks_closed,
        "tasks_breakdown"   : tc,
        "actions_proposed"  : actions_proposed,
        "top_belief_change" : top_belief_change,
        "top_reason"        : reason_for_change,
        "all_touched"       : touched,
    }

    return jsonify({"status": "ok", "summary": summary})


# ── 2. Thesis Cards ───────────────────────────────────────────────────────────

@terminal_bp.route("/terminal/theses")
def thesis_cards():
    """
    Returns thesis rows for the card display.
    Adjust table name / columns to your schema.
    Expected columns: thesis_key, confidence, status, last_update_reason,
                      evidence_count (or similar).
    """
    rows = _query(
        """
        SELECT
            thesis_key,
            confidence,
            status,
            last_update_reason,
            evidence_count,
            updated_at,
            timeframe,
            terminal_risk,
            watchlist_suggestion
        FROM   theses
        ORDER  BY confidence DESC, updated_at DESC
        LIMIT  30
        """,
    )

    # Normalise NULLs
    for r in rows:
        r["confidence"]         = round(float(r.get("confidence") or 0), 2)
        r["evidence_count"]     = r.get("evidence_count") or 0
        r["status"]             = r.get("status") or "active"
        r["last_update_reason"] = r.get("last_update_reason") or ""
        r["timeframe"]          = r.get("timeframe") or ""
        r["terminal_risk"]      = r.get("terminal_risk") or ""
        r["watchlist_suggestion"] = r.get("watchlist_suggestion") or ""

    return jsonify({"status": "ok", "theses": rows})


# ── 3. Action Visibility ──────────────────────────────────────────────────────

@terminal_bp.route("/terminal/actions")
def action_list():
    """
    Returns proposed actions.
    Adjust table name / columns to your schema.
    Expected: action_type, status, reason, approval_state, created_at, run_id
    """
    rows = _query(
        """
        SELECT
            id,
            action_type,
            status,
            reason,
            approval_state,
            created_at,
            run_id,
            thesis_key
        FROM   action_proposals
        ORDER  BY created_at DESC
        LIMIT  50
        """,
    )

    for r in rows:
        r["action_type"]    = r.get("action_type") or "unknown"
        r["status"]         = r.get("status") or "pending"
        r["approval_state"] = r.get("approval_state") or "awaiting"
        r["reason"]         = r.get("reason") or ""

    return jsonify({"status": "ok", "actions": rows})


# ── 4. Why-this-happened drilldown ────────────────────────────────────────────

@terminal_bp.route("/terminal/drilldown/<path:thesis_key>")
def drilldown(thesis_key):
    """
    Returns the full article → cluster → thesis → reasoning chain → action chain
    for a given thesis_key.
    """
    import json

    # a) Get thesis
    thesis = _query_one(
        "SELECT * FROM theses WHERE thesis_key = ? LIMIT 1",
        (thesis_key,),
    )

    # b) Get all reasoning chains for this thesis (latest 5)
    chains = _query(
        """
        SELECT id, article_id, thesis_key, terminal_risk,
               watchlist_suggestion, chain, created_at
        FROM   agent_reasoning
        WHERE  thesis_key = ?
        ORDER  BY created_at DESC
        LIMIT  5
        """,
        (thesis_key,),
    )

    # c) For each chain, pull the article headline
    article_ids = list({c["article_id"] for c in chains if c.get("article_id")})
    articles = {}
    if article_ids:
        placeholders = ",".join("?" * len(article_ids))
        art_rows = _query(
            f"SELECT id, headline, cluster_id, published_at FROM articles WHERE id IN ({placeholders})",
            tuple(article_ids),
        )
        articles = {a["id"]: a for a in art_rows}

    # d) Get cluster info
    cluster_ids = list({
        articles[aid]["cluster_id"]
        for aid in article_ids
        if aid in articles and articles[aid].get("cluster_id")
    })
    clusters = {}
    if cluster_ids:
        placeholders = ",".join("?" * len(cluster_ids))
        cl_rows = _query(
            f"SELECT id, label, article_count FROM clusters WHERE id IN ({placeholders})",
            tuple(cluster_ids),
        )
        clusters = {c["id"]: c for c in cl_rows}

    # e) Get related actions
    actions = _query(
        """
        SELECT id, action_type, status, reason, approval_state, created_at
        FROM   action_proposals
        WHERE  thesis_key = ?
        ORDER  BY created_at DESC
        LIMIT  5
        """,
        (thesis_key,),
    )

    # f) Build drilldown payload
    drilldown_chains = []
    for ch in chains:
        raw_chain = _safe_json(ch.get("chain"))
        hops = raw_chain if isinstance(raw_chain, list) else []
        article = articles.get(ch.get("article_id"), {})
        cluster_id = article.get("cluster_id")
        cluster = clusters.get(cluster_id, {})

        drilldown_chains.append({
            "reasoning_id"       : ch["id"],
            "created_at"         : ch["created_at"],
            "article"            : {
                "id"           : article.get("id"),
                "headline"     : article.get("headline", "Unknown headline"),
                "published_at" : article.get("published_at"),
            },
            "cluster"            : {
                "id"            : cluster.get("id"),
                "label"         : cluster.get("label", "Unknown cluster"),
                "article_count" : cluster.get("article_count", 0),
            },
            "reasoning_chain"    : hops,
            "terminal_risk"      : ch.get("terminal_risk", ""),
            "watchlist_suggestion": ch.get("watchlist_suggestion", ""),
        })

    return jsonify({
        "status"     : "ok",
        "thesis_key" : thesis_key,
        "thesis"     : thesis,
        "chains"     : drilldown_chains,
        "actions"    : actions,
    })


# ── 5. Before/After Run Diff ──────────────────────────────────────────────────

@terminal_bp.route("/terminal/diff")
def run_diff():
    """
    Compares the last two agent_loop journal entries.
    Returns what changed in beliefs (thesis confidence), tasks, actions.
    """
    rows = _query(
        """
        SELECT id, run_id, created_at, metrics
        FROM   agent_journal
        WHERE  journal_type = 'agent_loop'
        ORDER  BY id DESC
        LIMIT  2
        """,
    )

    if len(rows) < 2:
        return jsonify({
            "status" : "ok",
            "message": "Need at least two runs to diff.",
            "diff"   : None,
        })

    latest, prev = rows[0], rows[1]
    lm = _safe_json(latest.get("metrics", {}))
    pm = _safe_json(prev.get("metrics", {}))

    def _thesis_conf_map(journal_metrics):
        """
        Try to extract per-thesis confidence from metrics.
        Falls back to aggregate if individual not stored.
        """
        tu = journal_metrics.get("thesis_updates", {})
        touched = tu.get("touched", [])
        return {t: None for t in touched}   # placeholder – enrich if stored

    # ── belief diff ──────────────────────────────────────────────────────────
    prev_touched    = set(_safe_json(pm.get("thesis_updates", {})).get("touched", []))
    latest_touched  = set(_safe_json(lm.get("thesis_updates", {})).get("touched", []))

    new_beliefs    = sorted(latest_touched - prev_touched)
    dropped_beliefs= sorted(prev_touched - latest_touched)
    kept_beliefs   = sorted(latest_touched & prev_touched)

    # ── numeric deltas ───────────────────────────────────────────────────────
    def _delta(key, sub=None):
        a = lm.get(key, {}) if sub is None else lm.get(key, {}).get(sub, 0)
        b = pm.get(key, {}) if sub is None else pm.get(key, {}).get(sub, 0)
        if isinstance(a, dict):
            return {k: a.get(k, 0) - b.get(k, 0) for k in set(list(a) + list(b))}
        return a - b

    ltu = lm.get("thesis_updates", {})
    ptu = pm.get("thesis_updates", {})
    ltc = lm.get("task_closures", {})
    ptc = pm.get("task_closures", {})

    diff = {
        "prev_run"  : {"run_id": prev.get("run_id"),   "at": prev.get("created_at")},
        "latest_run": {"run_id": latest.get("run_id"), "at": latest.get("created_at")},

        "beliefs": {
            "new_theses_touched"    : new_beliefs,
            "dropped_from_touch"    : dropped_beliefs,
            "still_active"          : kept_beliefs,
            "upsert_delta"          : ltu.get("upserts", 0) - ptu.get("upserts", 0),
            "confidence_update_delta": ltu.get("confidence_updates", 0) - ptu.get("confidence_updates", 0),
        },

        "tasks": {
            "stale_delta"      : ltc.get("stale", 0)       - ptc.get("stale", 0),
            "superseded_delta" : ltc.get("superseded", 0)  - ptc.get("superseded", 0),
            "completed_delta"  : ltc.get("completed", 0)   - ptc.get("completed", 0),
            "contradicted_delta": ltc.get("contradicted", 0) - ptc.get("contradicted", 0),
        },

        "actions": {
            "proposals_delta": (
                lm.get("action_proposals_created", 0) -
                pm.get("action_proposals_created", 0)
            ),
        },

        "articles": {
            "fetched_delta": lm.get("items_fetched", 0) - pm.get("items_fetched", 0),
            "kept_delta"   : lm.get("items_kept", 0)    - pm.get("items_kept", 0),
        },
    }

    return jsonify({"status": "ok", "diff": diff})

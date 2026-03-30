from datetime import datetime, timezone


class AutonomyReporter:
    """
    After each run, write a plain-English self-report.
    """

    def generate_run_report(self, db_path: str, run_id: int, metrics: dict) -> str:
        import sqlite3

        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        goals = metrics.get("run_goals", []) or []
        research = metrics.get("active_research", {}) or {}
        rule_learn = metrics.get("rule_learning", {}) or {}
        actions = metrics.get("actions_executed", {}) or {}
        thesis_updates = metrics.get("thesis_updates", {}) or {}
        chains = int(metrics.get("reasoning_chains_built", 0) or 0)
        anomalies = int(metrics.get("anomalies_detected", 0) or 0)
        duration = float(metrics.get("duration_seconds", 0.0) or 0.0)

        thesis_count = int(
            conn.execute("SELECT COUNT(*) FROM agent_theses WHERE status != 'superseded'").fetchone()[0] or 0
        )
        top_thesis = conn.execute(
            """
            SELECT thesis_key, confidence
            FROM agent_theses
            WHERE status != 'superseded'
            ORDER BY confidence DESC
            LIMIT 1
            """
        ).fetchone()
        conn.close()

        now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
        lines = [
            f"## GeoClaw Autonomy Report — Run #{run_id}",
            f"*{now} | Duration: {round(duration)}s*",
            "",
            "### What I decided to focus on this run",
        ]
        if goals:
            lines.extend([f"- {goal}" for goal in goals[:4]])
        else:
            lines.append("- No specific goals set (reactive mode)")

        lines.extend(["", "### What I searched for"])
        if int(research.get("searches_done", 0) or 0) > 0:
            lines.append(
                f"- Conducted {int(research.get('searches_done', 0) or 0)} web searches, "
                f"found {int(research.get('articles_found', 0) or 0)} articles, "
                f"saved {int(research.get('articles_saved', 0) or 0)} for reasoning"
            )
            lines.append(f"- Research need: {int(research.get('needs_found', 0) or 0)} knowledge gaps identified")
        else:
            lines.append("- No active web searches this run (RSS articles were sufficient)")

        lines.extend(["", "### What I reasoned about"])
        lines.append(f"- Built {chains} reasoning chains across articles")
        lines.append(f"- Updated {int(thesis_updates.get('upserts', 0) or 0)} thesis confidence values")
        touched = list(thesis_updates.get("touched", []) or [])
        if touched:
            lines.append(f"- Touched theses: {', '.join(str(item)[:40] for item in touched[:3])}")

        lines.extend(["", "### What I learned"])
        if int(rule_learn.get("new_rules", 0) or 0) > 0:
            lines.append(f"- Discovered {int(rule_learn.get('new_rules', 0) or 0)} new reasoning rule(s) from prediction history")
        if int(rule_learn.get("updated_rules", 0) or 0) > 0:
            lines.append(f"- Updated {int(rule_learn.get('updated_rules', 0) or 0)} existing rule(s) with new accuracy data")
        if not int(rule_learn.get("new_rules", 0) or 0) and not int(rule_learn.get("updated_rules", 0) or 0):
            lines.append("- No new rules this run (insufficient verified predictions)")

        lines.extend(["", "### What I did (actions executed)"])
        auto_exec = int(actions.get("auto", 0) or 0) if isinstance(actions, dict) else 0
        manual_exec = int(actions.get("manual", 0) or 0) if isinstance(actions, dict) else 0
        if auto_exec + manual_exec > 0:
            lines.append(f"- Auto-executed {auto_exec} safe action(s)")
            lines.append(f"- Ran {manual_exec} user-approved action(s)")
        else:
            lines.append("- No actions executed this run")

        lines.extend(["", "### Current state"])
        lines.append(f"- {thesis_count} active theses")
        if top_thesis:
            lines.append(f"- Highest confidence: {round(float(top_thesis['confidence'] or 0.0) * 100)}% on '{str(top_thesis['thesis_key'] or '')[:80]}'")
        if anomalies > 0:
            lines.append(f"- {anomalies} anomalies detected")

        lines.extend(["", "---", f"*GeoClaw autonomous agent | Run #{run_id}*"])
        return "\n".join(lines)

    def save_report(self, db_path: str, run_id: int, report_text: str):
        import sqlite3

        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO agent_briefings (
                briefing_text, generated_at, thesis_count, contradiction_count, chain_count, action_count, run_id, format
            )
            VALUES (?, ?, 0, 0, 0, 0, ?, 'autonomy_report')
            """,
            (str(report_text or ""), now, int(run_id or 0)),
        )
        conn.commit()
        conn.close()

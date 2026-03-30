import sqlite3
from typing import Dict, List


class GoalPlanner:
    def generate_run_goals(self, db_path: str, run_id: int) -> List[Dict]:
        goals = []
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row

        high_conf = conn.execute(
            """
            SELECT t.thesis_key, t.confidence, t.terminal_risk
            FROM agent_theses t
            LEFT JOIN thesis_predictions p
              ON p.thesis_key = t.thesis_key
             AND p.outcome = 'pending'
            WHERE t.confidence >= 0.75
              AND t.status != 'superseded'
              AND p.id IS NOT NULL
            ORDER BY t.confidence DESC
            LIMIT 2
            """
        ).fetchall()
        for row in high_conf:
            goals.append(
                {
                    "goal": f"Verify: {str(row['thesis_key'] or '')[:80]}",
                    "type": "verification",
                    "priority": 1,
                    "search_query": str(row["thesis_key"] or "")[:60] + " latest update",
                    "thesis_key": row["thesis_key"],
                }
            )

        falling = conn.execute(
            """
            SELECT thesis_key, confidence, confidence_velocity
            FROM agent_theses
            WHERE confidence_velocity < -0.03
              AND status != 'superseded'
            ORDER BY confidence_velocity ASC
            LIMIT 2
            """
        ).fetchall()
        for row in falling:
            goals.append(
                {
                    "goal": f"Explain decline: {str(row['thesis_key'] or '')[:60]}",
                    "type": "explanation",
                    "priority": 2,
                    "search_query": str(row["thesis_key"] or "")[:50] + " why declining news",
                    "thesis_key": row["thesis_key"],
                }
            )

        try:
            from services.macro_calendar import MacroCalendar

            upcoming = MacroCalendar().get_upcoming(days_ahead=3)
            for event in upcoming[:2]:
                goals.append(
                    {
                        "goal": f"Pre-position for: {event['name']}",
                        "type": "calendar",
                        "priority": 2,
                        "search_query": event["name"] + " preview forecast expectations",
                        "thesis_key": "",
                    }
                )
        except Exception:
            pass

        contradictions = conn.execute(
            """
            SELECT thesis_key
            FROM contradictions
            WHERE resolved = 0
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchall()
        conn.close()
        for row in contradictions:
            goals.append(
                {
                    "goal": f"Resolve contradiction: {str(row['thesis_key'] or '')[:60]}",
                    "type": "resolution",
                    "priority": 2,
                    "search_query": str(row["thesis_key"] or "")[:60] + " latest evidence",
                    "thesis_key": row["thesis_key"],
                }
            )
        goals.sort(key=lambda item: (int(item.get("priority", 9)), str(item.get("goal", ""))))
        return goals[:5]

    def log_goals(self, goals: List[Dict], db_path: str, run_id: int):
        try:
            from services.agent_memory import AgentMemory

            AgentMemory(db_path).remember(
                "decision",
                "run_goals",
                {"goals": [item.get("goal", "") for item in goals], "run_id": run_id},
                importance=0.6,
                run_id=run_id,
            )
        except Exception:
            pass

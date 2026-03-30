import sqlite3
from typing import Dict


class BudgetManager:
    """
    Allocate a finite per-run LLM budget to the highest-priority theses.
    """

    def allocate_budget(self, db_path: str, total_budget: int) -> Dict:
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        theses = conn.execute(
            """
            SELECT thesis_key, confidence, terminal_risk, evidence_count, confidence_velocity, status
            FROM agent_theses
            WHERE status != 'superseded'
            ORDER BY confidence DESC
            LIMIT 50
            """
        ).fetchall()
        contradictions = conn.execute(
            """
            SELECT thesis_key
            FROM contradictions
            WHERE resolved = 0
            LIMIT 5
            """
        ).fetchall()
        conn.close()

        contradiction_keys = {str(row["thesis_key"] or "") for row in contradictions}
        scored = []
        for thesis in theses:
            score = 0
            if str(thesis["terminal_risk"] or "").startswith("HIGH"):
                score += 40
            if float(thesis["confidence_velocity"] or 0.0) < -0.03:
                score += 30
            if str(thesis["thesis_key"] or "") in contradiction_keys:
                score += 50
            if int(thesis["evidence_count"] or 0) < 2:
                score += 20
            if float(thesis["confidence"] or 0.0) < 0.40 and str(thesis["status"] or "") == "active":
                score += 15
            scored.append((str(thesis["thesis_key"] or ""), score))

        scored.sort(key=lambda item: item[1], reverse=True)
        allocation = {}
        remaining = max(0, int(total_budget or 0))
        for thesis_key, score in scored:
            if remaining <= 0:
                break
            calls = 1 if int(score) >= 30 else 0
            if calls > 0:
                allocation[thesis_key] = calls
                remaining -= calls
        return {
            "allocation": allocation,
            "total_budget": int(total_budget or 0),
            "allocated": sum(allocation.values()),
            "unallocated": remaining,
            "priority_count": len(allocation),
        }

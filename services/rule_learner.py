import json
import logging
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List


logger = logging.getLogger("geoclaw.rule_learner")

MIN_VERIFICATIONS_FOR_NEW_RULE = 3
MIN_ACCURACY_FOR_PROMOTION = 0.65
MAX_LEARNED_DELTA = 0.12


class RuleLearner:
    """
    The agent's self-improvement mechanism.
    Analyses its own prediction history to discover new reasoning patterns.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _tokenize(self, text: str) -> List[str]:
        skip = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "will",
            "may",
            "might",
            "monitor",
            "watch",
            "this",
            "that",
            "and",
            "or",
            "for",
            "in",
            "on",
            "at",
            "to",
            "of",
            "with",
            "from",
            "if",
            "as",
            "be",
            "been",
            "matter",
            "context",
            "follow",
            "followup",
            "detect",
            "detected",
            "signal",
            "immediately",
            "soon",
            "risk",
            "off",
            "upside",
            "downside",
            "tone",
        }
        words = re.findall(r"\b[a-z]{3,}\b", str(text or "").lower())
        return [word for word in words if word not in skip]

    def analyse_prediction_history(self) -> Dict:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        predictions = conn.execute(
            """
            SELECT thesis_key, predicted_direction, actual_change_pct,
                   outcome, symbol, confidence_at_prediction
            FROM thesis_predictions
            WHERE outcome IN ('verified', 'refuted')
              AND predicted_at >= datetime('now', '-30 days')
            """
        ).fetchall()
        conn.close()

        if len(predictions) < 5:
            logger.info("Rule learner: insufficient prediction history (%s)", len(predictions))
            return {"candidate_rules": [], "analysed": len(predictions)}

        verified_keywords = Counter()
        refuted_keywords = Counter()
        total_by_keyword = Counter()

        tokenized_predictions = []
        for prediction in predictions:
            tokens = self._tokenize(prediction["thesis_key"])
            tokenized_predictions.append((dict(prediction), tokens))
            for token in set(tokens):
                total_by_keyword[token] += 1
                if prediction["outcome"] == "verified":
                    verified_keywords[token] += 1
                else:
                    refuted_keywords[token] += 1

        candidates = []
        for keyword, verified_count in verified_keywords.most_common(20):
            total = int(total_by_keyword[keyword] or 0)
            refuted_count = int(refuted_keywords.get(keyword, 0) or 0)
            if total < MIN_VERIFICATIONS_FOR_NEW_RULE:
                continue
            accuracy = verified_count / total
            if accuracy < MIN_ACCURACY_FOR_PROMOTION:
                continue

            matching = [pred for pred, tokens in tokenized_predictions if keyword in tokens and pred["outcome"] == "verified"]
            if matching:
                avg_move = sum(abs(float(pred.get("actual_change_pct") or 0.0)) for pred in matching) / len(matching)
            else:
                avg_move = 0.0
            delta = min(MAX_LEARNED_DELTA, max(0.02, avg_move * 0.025))
            candidates.append(
                {
                    "keyword": keyword,
                    "confidence_delta": round(delta, 3),
                    "verified_count": verified_count,
                    "refuted_count": refuted_count,
                    "total_predictions": total,
                    "accuracy_pct": round(accuracy * 100, 1),
                    "avg_price_move_pct": round(avg_move, 2),
                    "mechanism": f"Keyword '{keyword}' predicts market moves with {round(accuracy * 100)}% accuracy ({total} predictions)",
                    "market_implication": f"Observed {verified_count}/{total} verified predictions when '{keyword}' is present",
                }
            )
        return {"candidate_rules": candidates, "analysed": len(predictions)}

    def write_learned_rules(self) -> Dict:
        analysis = self.analyse_prediction_history()
        candidates = analysis.get("candidate_rules", [])
        if not candidates:
            return {
                "new_rules": 0,
                "updated_rules": 0,
                "retired_rules": 0,
                "analysed_predictions": analysis.get("analysed", 0),
                "candidates_considered": 0,
            }

        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        now = datetime.now(timezone.utc).isoformat()
        new_rules = 0
        updated_rules = 0

        for candidate in candidates:
            keyword = candidate["keyword"]
            if self._is_in_hardcoded_rules(keyword):
                continue

            existing = conn.execute("SELECT * FROM learned_rules WHERE keyword = ?", (keyword,)).fetchone()
            if existing:
                new_accuracy = float(candidate["accuracy_pct"] or 0.0)
                new_count = int(existing["verification_count"] or 0) + int(candidate["verified_count"] or 0)
                new_status = str(existing["status"] or "probationary")
                if new_status == "probationary" and new_accuracy >= MIN_ACCURACY_FOR_PROMOTION * 100 and new_count >= MIN_VERIFICATIONS_FOR_NEW_RULE * 2:
                    new_status = "active"
                conn.execute(
                    """
                    UPDATE learned_rules
                    SET verification_count = ?,
                        accuracy_pct = ?,
                        last_verified = ?,
                        status = ?,
                        confidence_delta = ?,
                        mechanism = ?,
                        market_implication = ?
                    WHERE keyword = ?
                    """,
                    (
                        new_count,
                        new_accuracy,
                        now,
                        new_status,
                        float(candidate["confidence_delta"] or 0.02),
                        str(candidate["mechanism"] or ""),
                        str(candidate["market_implication"] or ""),
                        keyword,
                    ),
                )
                updated_rules += 1
            else:
                status = "probationary"
                if float(candidate["accuracy_pct"] or 0.0) >= 80.0 and int(candidate["total_predictions"] or 0) >= 5:
                    status = "active"
                conn.execute(
                    """
                    INSERT INTO learned_rules (
                        keyword, confidence_delta, timeframe, mechanism,
                        market_implication, discovered_from, verification_count,
                        accuracy_pct, created_at, last_verified, status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        keyword,
                        float(candidate["confidence_delta"] or 0.02),
                        "days",
                        str(candidate["mechanism"] or ""),
                        str(candidate["market_implication"] or ""),
                        json.dumps(
                            {
                                "accuracy_pct": candidate["accuracy_pct"],
                                "verified": candidate["verified_count"],
                                "total": candidate["total_predictions"],
                            }
                        ),
                        int(candidate["verified_count"] or 0),
                        float(candidate["accuracy_pct"] or 0.0),
                        now,
                        now,
                        status,
                    ),
                )
                new_rules += 1

        conn.execute(
            """
            UPDATE learned_rules
            SET status = 'retired'
            WHERE accuracy_pct < 40
              AND verification_count >= 5
              AND status = 'active'
            """
        )
        retired_rules = int(conn.execute("SELECT changes()").fetchone()[0] or 0)
        conn.commit()
        conn.close()
        return {
            "new_rules": new_rules,
            "updated_rules": updated_rules,
            "retired_rules": retired_rules,
            "analysed_predictions": analysis.get("analysed", 0),
            "candidates_considered": len(candidates),
        }

    def _is_in_hardcoded_rules(self, keyword: str) -> bool:
        try:
            from services.rule_engine import RULES

            return any(str(rule[0] or "") == str(keyword or "") for rule in RULES)
        except Exception:
            return False

    def get_active_learned_rules(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT keyword, confidence_delta, timeframe, mechanism, market_implication,
                   verification_count, accuracy_pct, status, created_at, last_verified
            FROM learned_rules
            WHERE status = 'active'
            ORDER BY accuracy_pct DESC, verification_count DESC, id DESC
            """
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

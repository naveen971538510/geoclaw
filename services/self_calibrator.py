import sqlite3

from services.logging_service import get_logger
from services.price_feed import PriceFeed


logger = get_logger("self_calibrator")


class SelfCalibrator:
    def evaluate_past_theses(self, db_path: str) -> dict:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            old_theses = conn.execute(
                """
                SELECT thesis_key, confidence, terminal_risk
                FROM agent_theses
                WHERE created_at < datetime('now', '-24 hours')
                  AND status NOT IN ('superseded')
                  AND terminal_risk IN ('HIGH', 'MEDIUM')
                LIMIT 20
                """
            ).fetchall()
            conn.close()

            pf = PriceFeed()
            verified = 0
            refuted = 0
            unknown = 0

            for thesis in old_theses:
                key = str(thesis["thesis_key"] or "").lower()
                prices = pf.get_thesis_relevant_prices(thesis["thesis_key"])
                vix = next((price for price in prices if price.get("symbol") == "^VIX"), None)
                if not vix:
                    unknown += 1
                    continue

                risk_up = any(word in key for word in ["war", "missile", "sanction", "crisis", "default", "strike"])
                risk_dn = any(word in key for word in ["ceasefire", "peace", "resolved", "rate cut"])
                vix_up = float(vix.get("change_pct", 0.0) or 0.0) > 0.5
                vix_dn = float(vix.get("change_pct", 0.0) or 0.0) < -0.5

                if (risk_up and vix_up) or (risk_dn and vix_dn):
                    verified += 1
                elif (risk_up and vix_dn) or (risk_dn and vix_up):
                    refuted += 1
                else:
                    unknown += 1

            accuracy = verified / max(verified + refuted, 1) * 100
            return {
                "verified": verified,
                "refuted": refuted,
                "unknown": unknown,
                "total_evaluated": verified + refuted + unknown,
                "accuracy_pct": round(accuracy, 1),
            }
        except Exception as exc:
            logger.error("Self-calibration failed: %s", exc)
            return {"error": str(exc), "accuracy_pct": 0}

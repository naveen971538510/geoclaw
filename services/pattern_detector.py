import sqlite3

from services.logging_service import get_logger


logger = get_logger("patterns")

NARRATIVES = {
    "Middle East Conflict": ["iran", "missile", "strike", "oil", "hormuz", "israel", "hamas", "hezbollah"],
    "US Monetary Policy": ["fed", "rate", "powell", "inflation", "treasury", "yield", "fomc"],
    "China Risk": ["china", "xi", "taiwan", "trade war", "decoupling", "yuan", "beijing"],
    "Energy Markets": ["oil", "opec", "crude", "energy", "brent", "wti", "gas", "pipeline"],
    "EM / Dollar Stress": ["emerging", "dollar", "em ", "debt", "default", "currency", "devalue"],
    "Recession Risk": ["recession", "gdp", "pmi", "unemployment", "contraction", "slowdown"],
    "Geopolitical Conflict": ["war", "sanction", "tariff", "embargo", "conflict", "coup", "ceasefire"],
    "Safe Haven Demand": ["gold", "vix", "fear", "uncertainty", "refuge", "safe haven", "shelter"],
}


class PatternDetector:
    def detect_narrative_cluster(self, theses: list) -> list:
        clusters = []
        for narrative, keywords in NARRATIVES.items():
            matching = [
                thesis
                for thesis in theses
                if any(keyword in str((thesis or {}).get("thesis_key", "") or "").lower() for keyword in keywords)
            ]
            if not matching:
                continue
            avg_conf = sum(float((thesis or {}).get("confidence", 0.0) or 0.0) for thesis in matching) / len(matching)
            avg_vel = sum(float((thesis or {}).get("confidence_velocity", 0.0) or 0.0) for thesis in matching) / len(matching)
            clusters.append(
                {
                    "narrative": narrative,
                    "thesis_count": len(matching),
                    "avg_confidence": round(avg_conf, 3),
                    "trend": "rising" if avg_vel > 0.01 else ("falling" if avg_vel < -0.01 else "stable"),
                    "top_thesis": max(matching, key=lambda x: float((x or {}).get("confidence", 0.0) or 0.0), default={}).get("thesis_key", ""),
                }
            )
        return sorted(clusters, key=lambda x: x["avg_confidence"], reverse=True)

    def detect_momentum_shifts(self, db_path: str) -> list:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT thesis_key, confidence, confidence_velocity, status, terminal_risk
                FROM agent_theses
                WHERE ABS(COALESCE(confidence_velocity, 0)) > 0.03
                  AND status NOT IN ('superseded')
                ORDER BY ABS(COALESCE(confidence_velocity, 0)) DESC
                LIMIT 10
                """
            ).fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as exc:
            logger.error("Momentum detection failed: %s", exc)
            return []

    def compute_market_regime(self, theses: list, prices: dict = None) -> dict:
        if not theses:
            return {"regime": "NEUTRAL", "description": "No thesis data.", "risk_level": "LOW", "avg_thesis_confidence": 0}
        avg_conf = sum(float((thesis or {}).get("confidence", 0.5) or 0.5) for thesis in theses) / len(theses)
        high_risk = sum(1 for thesis in theses if "HIGH" in str((thesis or {}).get("terminal_risk", "") or ""))
        vix_price = float(((prices or {}).get("^VIX", {}) or {}).get("price", 18) or 18)

        if high_risk >= 3 or (vix_price and vix_price > 30):
            regime, desc, risk = "CRISIS", "Multiple HIGH-risk signals. Extreme caution.", "EXTREME"
        elif high_risk >= 2 or avg_conf > 0.72:
            regime, desc, risk = "RISK-OFF", "Elevated risk. Defensive positioning advisable.", "HIGH"
        elif avg_conf > 0.58:
            regime, desc, risk = "CAUTIOUS", "Developing signals. Monitor for escalation.", "MEDIUM"
        else:
            regime, desc, risk = "NEUTRAL", "No dominant risk narrative. Normal monitoring.", "LOW"

        return {
            "regime": regime,
            "description": desc,
            "risk_level": risk,
            "avg_thesis_confidence": round(avg_conf, 3),
            "high_risk_thesis_count": high_risk,
        }

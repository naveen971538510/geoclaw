import sqlite3


SECTOR_THESIS_MAP = {
    "Energy": ["oil", "crude", "opec", "brent", "energy", "gas", "pipeline"],
    "Defense": ["war", "missile", "conflict", "nato", "military", "defense"],
    "Gold/Metals": ["gold", "precious", "safe haven", "metal", "silver", "copper"],
    "Banks/Financials": ["rate", "fed", "yield", "banking", "credit", "spread", "ecb"],
    "Tech": ["chip", "semiconductor", "ai", "tech", "china decoupling", "taiwan"],
    "Consumer": ["inflation", "consumer", "spending", "retail", "recession"],
    "Utilities": ["recession", "defensive", "safe haven", "risk off"],
    "EM": ["emerging market", "dollar", "em ", "developing", "brazil", "india"],
    "Agriculture": ["wheat", "food", "supply chain", "drought", "grain"],
    "Healthcare": ["recession", "defensive", "virus", "pandemic"],
}

SECTOR_ETF_MAP = {
    "Energy": "XLE",
    "Defense": "ITA",
    "Gold/Metals": "GLD",
    "Banks/Financials": "XLF",
    "Tech": "QQQ",
    "Consumer": "XLY",
    "Utilities": "XLU",
    "EM": "EEM",
    "Agriculture": "MOO",
    "Healthcare": "XLV",
}


class SectorRotation:
    def compute_signals(self, db_path: str) -> dict:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        theses = conn.execute(
            """
            SELECT thesis_key, confidence, terminal_risk, confidence_velocity
            FROM agent_theses
            WHERE COALESCE(status, '') != 'superseded' AND COALESCE(confidence, 0) >= 0.50
            """
        ).fetchall()
        conn.close()

        sector_signals = {}
        for sector, keywords in SECTOR_THESIS_MAP.items():
            matching = []
            for thesis in theses:
                text = str(thesis["thesis_key"] or "").lower()
                if any(keyword in text for keyword in keywords):
                    matching.append(dict(thesis))

            if not matching:
                continue

            avg_conf = sum(float(item.get("confidence", 0.0) or 0.0) for item in matching) / len(matching)
            avg_velocity = sum(float(item.get("confidence_velocity", 0.0) or 0.0) for item in matching) / len(matching)
            high_risk = sum(1 for item in matching if str(item.get("terminal_risk", "")).upper().startswith("HIGH"))
            signal_score = avg_conf * 100 + avg_velocity * 200 + high_risk * 15

            if signal_score > 65:
                signal = "OVERWEIGHT"
            elif signal_score < 35:
                signal = "UNDERWEIGHT"
            else:
                signal = "NEUTRAL"

            sector_signals[sector] = {
                "sector": sector,
                "etf": SECTOR_ETF_MAP.get(sector, ""),
                "signal": signal,
                "signal_score": round(signal_score, 1),
                "avg_confidence": round(avg_conf, 3),
                "velocity_trend": "rising" if avg_velocity > 0.01 else ("falling" if avg_velocity < -0.01 else "stable"),
                "supporting_theses": len(matching),
                "high_risk_count": high_risk,
                "top_thesis": matching[0]["thesis_key"][:80] if matching else "",
            }

        return dict(sorted(sector_signals.items(), key=lambda item: item[1]["signal_score"], reverse=True))

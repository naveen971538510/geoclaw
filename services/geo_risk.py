import sqlite3


REGION_KEYWORDS = {
    "Middle East": ["iran", "iraq", "israel", "syria", "saudi", "yemen", "lebanon", "hormuz", "gulf"],
    "Eastern Europe": ["russia", "ukraine", "nato", "moldova", "belarus", "donbas", "crimea"],
    "Asia Pacific": ["china", "taiwan", "japan", "korea", "south china sea", "asean", "india"],
    "North America": ["usa", "fed", "treasury", "congress", "washington", "mexico", "canada"],
    "Western Europe": ["ecb", "eu", "france", "germany", "italy", "spain", "brexit", "boe"],
    "Latin America": ["brazil", "argentina", "venezuela", "colombia", "mexico", "latam"],
    "Africa": ["nigeria", "south africa", "egypt", "kenya", "ethiopia", "sahel"],
    "South Asia": ["india", "pakistan", "bangladesh", "sri lanka", "afghanistan"],
    "Global/Multi": ["g7", "g20", "imf", "world bank", "wto", "un", "opec", "nato", "global"],
}


class GeoRisk:
    def compute_region_risk(self, db_path: str) -> dict:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        theses = conn.execute(
            """
            SELECT thesis_key, confidence, terminal_risk, confidence_velocity
            FROM agent_theses
            WHERE COALESCE(status, '') != 'superseded'
            """
        ).fetchall()
        articles = conn.execute(
            """
            SELECT headline, fetched_at
            FROM ingested_articles
            WHERE COALESCE(fetched_at, '') >= datetime('now','-24 hours')
            """
        ).fetchall()
        conn.close()

        region_scores = {}
        for region, keywords in REGION_KEYWORDS.items():
            matching_theses = []
            matching_articles = 0

            for thesis in theses:
                text = str(thesis["thesis_key"] or "").lower()
                if any(keyword in text for keyword in keywords):
                    matching_theses.append(dict(thesis))

            for article in articles:
                headline = str(article["headline"] or "").lower()
                if any(keyword in headline for keyword in keywords):
                    matching_articles += 1

            if not matching_theses and matching_articles == 0:
                continue

            avg_conf = sum(float(item.get("confidence", 0.0) or 0.0) for item in matching_theses) / max(len(matching_theses), 1)
            high_risk = sum(1 for item in matching_theses if str(item.get("terminal_risk", "")).upper().startswith("HIGH"))
            avg_velocity = sum(float(item.get("confidence_velocity", 0.0) or 0.0) for item in matching_theses) / max(len(matching_theses), 1)
            risk_score = min(100, int(avg_conf * 60 + high_risk * 15 + matching_articles * 2))

            if risk_score >= 75:
                risk_level = "CRITICAL"
            elif risk_score >= 55:
                risk_level = "HIGH"
            elif risk_score >= 35:
                risk_level = "MEDIUM"
            else:
                risk_level = "LOW"

            region_scores[region] = {
                "region": region,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "thesis_count": len(matching_theses),
                "article_count_24h": matching_articles,
                "avg_confidence": round(avg_conf, 3),
                "high_risk_theses": high_risk,
                "velocity_trend": "rising" if avg_velocity > 0.01 else ("falling" if avg_velocity < -0.01 else "stable"),
                "top_thesis": matching_theses[0]["thesis_key"][:100] if matching_theses else "",
            }

        return dict(sorted(region_scores.items(), key=lambda item: item[1]["risk_score"], reverse=True))

import sqlite3


PERSONAS = {
    "bull": {
        "name": "GeoHawk (Bull)",
        "system": """You are GeoHawk, an aggressive bullish macro analyst.
You argue FOR the thesis being correct and the risk being real.
You emphasize escalation risk, supply disruption, and market impact.
Be direct, confident, and cite specific mechanisms.
2-3 sentences max. JSON only: {"argument": "...", "key_point": "..."}""",
    },
    "bear": {
        "name": "GeoDove (Bear)",
        "system": """You are GeoDove, a skeptical bearish analyst.
You argue AGAINST the thesis and why it might be overblown.
Emphasize de-escalation paths, market resilience, and prior false alarms.
Be direct, confident, and cite alternative interpretations.
2-3 sentences max. JSON only: {"argument": "...", "key_point": "..."}""",
    },
}


class DebateEngine:
    def __init__(self, db_path, llm_analyst=None):
        self.db_path = db_path
        self.llm = llm_analyst

    def debate_thesis(self, thesis_key: str) -> dict:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        thesis = conn.execute(
            "SELECT * FROM agent_theses WHERE thesis_key=? LIMIT 1",
            (thesis_key,),
        ).fetchone()
        recent_articles = conn.execute(
            """
            SELECT headline
            FROM ingested_articles
            WHERE headline LIKE ?
            ORDER BY published_at DESC, fetched_at DESC
            LIMIT 3
            """,
            (f"%{str(thesis_key or '')[:30]}%",),
        ).fetchall()
        conn.close()

        if not thesis:
            return {"error": "Thesis not found"}

        thesis_dict = dict(thesis)
        confidence_pct = round(float(thesis_dict.get("confidence", 0.0) or 0.0) * 100)
        evidence = int(thesis_dict.get("evidence_count", 0) or 0)
        context = (
            f"Thesis: {thesis_key}\n"
            f"Current confidence: {confidence_pct}%\n"
            f"Evidence articles: {evidence}\n"
            f"Recent headlines: {'; '.join([row['headline'] for row in recent_articles])}"
        )

        bull_arg, bull_mode = self._get_argument("bull", context, thesis_key, confidence_pct)
        bear_arg, bear_mode = self._get_argument("bear", context, thesis_key, confidence_pct)
        mode = "llm" if bull_mode == "llm" and bear_mode == "llm" else "rule_based"

        if confidence_pct >= 70:
            verdict = "Bull case prevails — high confidence supported by evidence."
            verdict_winner = "bull"
        elif confidence_pct <= 35:
            verdict = "Bear case prevails — evidence remains too thin for conviction."
            verdict_winner = "bear"
        else:
            verdict = "Debate is still balanced — this thesis needs more confirming evidence."
            verdict_winner = "neutral"

        return {
            "thesis_key": thesis_key,
            "confidence": confidence_pct,
            "bull": {"persona": PERSONAS["bull"]["name"], **bull_arg},
            "bear": {"persona": PERSONAS["bear"]["name"], **bear_arg},
            "verdict": verdict,
            "verdict_winner": verdict_winner,
            "evidence_count": evidence,
            "mode": mode,
        }

    def _get_argument(self, persona: str, context: str, thesis_key: str, confidence_pct: int):
        if self.llm and self.llm.available():
            try:
                import json
                import openai
                import os

                client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": PERSONAS[persona]["system"]},
                        {"role": "user", "content": context},
                    ],
                    max_tokens=200,
                    temperature=0.7,
                    response_format={"type": "json_object"},
                )
                result = json.loads(response.choices[0].message.content)
                return {
                    "argument": str(result.get("argument", "") or ""),
                    "key_point": str(result.get("key_point", "") or ""),
                }, "llm"
            except Exception:
                pass

        key = str(thesis_key or "").lower()
        if persona == "bull":
            if "iran" in key or "missile" in key or "hormuz" in key:
                return {
                    "argument": "Any Strait of Hormuz escalation directly threatens energy transit and can reprice volatility faster than spot oil currently implies. That asymmetric tail risk is exactly why this thesis deserves respect.",
                    "key_point": "Escalation risk is still underpriced",
                }, "rule_based"
            if "sanction" in key:
                return {
                    "argument": "Sanctions usually bite through financing, shipping, and settlement channels long before the full macro impact is visible in headlines. Markets often need several weeks to fully absorb that tightening effect.",
                    "key_point": "Sanctions create durable frictions",
                }, "rule_based"
            return {
                "argument": f"The thesis is already carrying {confidence_pct}% confidence, which suggests the evidence stack is broadening rather than fading. When multiple supporting signals align, markets usually move before the consensus narrative catches up.",
                "key_point": "Evidence breadth favors the thesis",
            }, "rule_based"

        if "iran" in key or "missile" in key:
            return {
                "argument": "Markets have seen many Iran escalation headlines that failed to produce lasting dislocation once cooler heads intervened. Unless the supply disruption becomes physical and persistent, this risk premium can fade quickly.",
                "key_point": "Headline risk may outrun realized damage",
            }, "rule_based"
        if "sanction" in key:
            return {
                "argument": "Sanctioned systems adapt more quickly than the first headlines suggest through rerouting, discounting, and alternate settlement channels. That makes the second-order market impact less certain than the thesis implies.",
                "key_point": "Adaptation can blunt the shock",
            }, "rule_based"
        return {
            "argument": "This thesis has not yet crossed the threshold where the counterargument disappears. Markets can stay resilient for longer than a clean narrative would suggest, especially when positioning is already defensive.",
            "key_point": "Resilience can outlast the narrative",
        }, "rule_based"

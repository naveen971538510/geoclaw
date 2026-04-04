import logging
import time
from typing import Dict, List, Tuple


logger = logging.getLogger("geoclaw.rule_engine")


RULES = [
    ("missile", +0.14, "immediate", "Missile strike signals active conflict escalation", "Safe-haven surge: gold, USD, bonds"),
    ("nuclear", +0.16, "immediate", "Nuclear threat elevates systemic risk to maximum", "Extreme risk-off: equities collapse, gold surges"),
    ("war", +0.12, "immediate", "Active armed conflict disrupts trade and supply chains", "Energy spike, USD safe-haven, EM sell-off"),
    ("strike", +0.10, "days", "Military or industrial strike disrupts operations", "Sector-specific volatility"),
    ("sanction", +0.11, "days", "Sanctions restrict capital flows and trade routes", "EM currency pressure, commodity rerouting"),
    ("iran", +0.10, "days", "Iran tensions affect Strait of Hormuz oil flow", "Brent crude spike, shipping risk premium"),
    ("default", +0.13, "days", "Sovereign default triggers systemic contagion risk", "Credit spreads widen, EM FX collapse"),
    ("tariff", +0.08, "weeks", "Tariffs raise input costs and inflation expectations", "Inflation repricing, USD strength"),
    ("recession", +0.10, "months", "Recession signals demand destruction and job losses", "Risk-off rotation, yield curve bull-flatten"),
    ("inflation", +0.07, "weeks", "Inflation data moves central bank rate expectations", "Bond yield repricing, growth stock pressure"),
    ("rate hike", +0.08, "weeks", "Rate hike tightens financial conditions", "Dollar strength, EM capital outflows"),
    ("rate cut", -0.06, "weeks", "Rate cut loosens financial conditions", "Growth assets rally, dollar weakens"),
    ("fed", +0.06, "weeks", "Fed guidance drives global liquidity expectations", "Dollar and Treasury yield direction set"),
    ("ecb", +0.05, "weeks", "ECB policy affects EUR zone capital flows", "EUR/USD and Bund yield reaction"),
    ("oil", +0.06, "days", "Oil price moves drive inflation and trade balances", "Energy sector, petrocurrency, transport costs"),
    ("gold", +0.05, "days", "Gold move signals risk sentiment shift", "Risk appetite indicator for broader market"),
    ("china", +0.07, "weeks", "China macro drives global commodity demand", "Iron ore, copper, EM export demand"),
    ("election", +0.06, "months", "Election creates policy uncertainty premium", "Sector rotation, currency volatility"),
    ("ceasefire", -0.08, "days", "Ceasefire reduces geopolitical risk premium", "Risk-on: equities recover, safe-haven unwind"),
    ("coup", +0.11, "immediate", "Coup creates immediate political instability", "EM asset sell-off, currency collapse"),
    ("crisis", +0.09, "days", "Crisis signals elevated systemic risk", "Broad risk-off, defensive positioning"),
    ("opec", +0.07, "weeks", "OPEC decision sets oil supply expectations", "Energy sector, USD petrodollar flows"),
    ("dollar", +0.05, "days", "Dollar move affects global trade and debt servicing", "EM, commodities, export competitiveness"),
    ("yield", +0.06, "weeks", "Yield move signals rate expectation shift", "Bond/equity relative value repricing"),
    ("pmi", +0.06, "weeks", "PMI signals manufacturing and service sector health", "Growth expectation update, currency move"),
    ("gdp", +0.07, "weeks", "GDP data confirms or challenges growth narrative", "Rate expectations, currency, equity sector"),
    ("bankruptcy", +0.09, "days", "Bankruptcy signals credit stress in sector", "Credit spreads, contagion to counterparties"),
    ("contagion", +0.10, "days", "Contagion risk implies cascading credit events", "Systemic risk premium, broad deleveraging"),
    ("unemployment", +0.07, "weeks", "Jobs data influences Fed and consumer spending", "Rate path, consumer discretionary"),
    ("trade war", +0.09, "weeks", "Trade war escalation disrupts global supply chains", "Multi-sector repricing, USD/CNY pressure"),
    ("supply chain", +0.06, "weeks", "Supply chain disruption raises costs and delays", "Margin compression, inflation persistence"),
    ("bitcoin", +0.04, "days", "Crypto market signals risk appetite or safe-haven", "Correlated with risk-on/risk-off mood"),
    ("bank", +0.05, "days", "Banking sector news implies systemic stress or growth", "Financial sector, credit availability"),
    ("debt ceiling", +0.08, "weeks", "Debt ceiling risk signals US fiscal credibility", "USD, Treasuries, risk-off globally"),
    ("rally", -0.04, "days", "Rally signals improving sentiment and risk appetite", "Equity upside, safe-haven unwind"),
    ("crash", +0.11, "immediate", "Crash signals acute market stress", "Liquidity crisis, forced selling"),
]

GEO_MAP = {
    "russia": "Eastern Europe/NATO",
    "ukraine": "Eastern Europe/NATO",
    "iran": "Middle East/Energy",
    "israel": "Middle East",
    "china": "Asia-Pacific/Trade",
    "taiwan": "Asia-Pacific/Tech",
    "north korea": "Asia-Pacific/Security",
    "saudi": "Middle East/Energy/OPEC",
    "india": "South Asia/EM",
    "brazil": "Latin America/EM",
    "turkey": "EMEA/EM",
}


class RuleEngine:
    def __init__(self):
        self._db_path = None
        self._learned_rules = []
        self._learned_rules_loaded_at = 0.0

    def load_learned_rules(self, db_path: str):
        if not db_path:
            return
        if self._learned_rules and (time.time() - float(self._learned_rules_loaded_at or 0.0)) < 300:
            return
        try:
            from services.rule_learner import RuleLearner

            self._learned_rules = RuleLearner(db_path).get_active_learned_rules()
            self._learned_rules_loaded_at = time.time()
            if self._learned_rules:
                logger.info("Rule engine loaded %s learned rules", len(self._learned_rules))
        except Exception as exc:
            logger.warning("Could not load learned rules: %s", exc)
            self._learned_rules = []

    def _text(self, article: Dict) -> str:
        headline = str(article.get("headline") or "").strip()
        body = str(article.get("body") or article.get("summary") or "").strip()
        return (headline + " " + body[:300]).lower()

    def derive_thesis_key(self, article: Dict) -> str:
        headline = str(article.get("headline") or "").strip()
        text = self._text(article)

        best_rule = None
        best_delta = 0.0
        for kw, delta, timeframe, mechanism, implication in RULES:
            if kw in text and abs(delta) > abs(best_delta):
                best_rule = (kw, delta, timeframe, mechanism, implication)
                best_delta = delta

        geo = "Global"
        for place, region in GEO_MAP.items():
            if place in text:
                geo = region
                break

        if best_rule:
            _kw, _delta, timeframe, mechanism, implication = best_rule
            return f"{mechanism}. {implication} — {geo} — {timeframe} horizon."

        truncated = headline[:120].rstrip(".").rstrip(",")
        truncated_clean = ''.join(c for c in truncated if ord(c) < 128).strip()
        if len(truncated_clean.split()) < 2:
            return ""  # Non-English headline — skip thesis key generation
        return f"Monitor: {truncated_clean}. Context unclear — watch for follow-up confirmation."

    def reason(self, article: Dict) -> Tuple[float, List[Dict]]:
        text = self._text(article)
        if self._db_path:
            self.load_learned_rules(self._db_path)
        matched_rules = []
        for kw, delta, timeframe, mechanism, implication in RULES:
            if kw in text:
                matched_rules.append((kw, delta, timeframe, mechanism, implication))

        if not matched_rules:
            pos_words = ["gain", "rally", "rise", "grow", "beat", "strong", "surge", "recover", "up"]
            neg_words = ["fall", "drop", "war", "crisis", "risk", "cut", "miss", "weak", "crash", "down"]
            pos = sum(1 for word in pos_words if word in text)
            neg = sum(1 for word in neg_words if word in text)
            if neg > pos:
                delta = -0.03
                mechanism = "Negative tone detected. Risk-off or downside implications may matter."
                implication = "Monitor for confirmation signals."
            elif pos > neg:
                delta = +0.05
                mechanism = "Positive tone detected. Market-sensitive upside narrative may matter."
                implication = "Watch for price action confirmation."
            else:
                delta = +0.01
                mechanism = "Mixed or neutral headline. Monitor context and follow-up developments."
                implication = "No immediate directional signal."

            chain = [
                {
                    "hop": 1,
                    "from": "headline sentiment",
                    "to": "market positioning",
                    "mechanism": mechanism,
                    "confidence": 0.5,
                    "timeframe": "days",
                },
                {
                    "hop": 2,
                    "from": "market positioning",
                    "to": "watch",
                    "mechanism": implication,
                    "confidence": 0.5,
                    "timeframe": "days",
                },
            ]
            return delta, chain

        total_delta = 0.0
        chain = [
            {
                "hop": 1,
                "from": "news event",
                "to": "market positioning",
                "mechanism": matched_rules[0][3],
                "confidence": 0.55,
                "timeframe": matched_rules[0][2],
            }
        ]

        for index, (kw, delta, timeframe, _mechanism, implication) in enumerate(matched_rules[:3]):
            total_delta += delta
            if index > 0:
                chain.append(
                    {
                        "hop": index + 2,
                        "from": "market positioning",
                        "to": kw + " exposure",
                        "mechanism": implication,
                        "confidence": round(0.5 + abs(delta), 2),
                        "timeframe": timeframe,
                    }
                )

        for learned in self._learned_rules[:8]:
            keyword = str(learned.get("keyword") or "").strip().lower()
            if not keyword or keyword not in text:
                continue
            learned_delta = float(learned.get("confidence_delta", 0.03) or 0.03)
            total_delta += learned_delta
            chain.append(
                {
                    "hop": len(chain) + 1,
                    "from": "learned pattern",
                    "to": keyword + " signal",
                    "mechanism": str(learned.get("mechanism") or "Learned from prediction history"),
                    "confidence": round(0.5 + abs(learned_delta), 2),
                    "timeframe": str(learned.get("timeframe") or "days"),
                    "rule_source": "learned",
                }
            )

        if len(chain) < 2:
            kw, delta, timeframe, mechanism, implication = matched_rules[0]
            chain.append(
                {
                    "hop": 2,
                    "from": "market positioning",
                    "to": kw + " exposure",
                    "mechanism": implication or mechanism,
                    "confidence": round(0.5 + abs(delta), 2),
                    "timeframe": timeframe,
                }
            )

        total_delta = max(-0.25, min(0.25, total_delta))
        return total_delta, chain

    def compute_terminal_risk(self, thesis_key: str, confidence: float, timeframe: str = "") -> str:
        tf = str(timeframe or "").lower()
        if confidence >= 0.80 and tf in ("immediate", "days"):
            return "HIGH — Immediate position risk. Review exposure."
        if confidence >= 0.70:
            return "MEDIUM — Elevated signal strength. Monitor closely."
        if confidence >= 0.55:
            return "LOW-MEDIUM — Developing signal. Watch for confirmation."
        return "LOW — Insufficient evidence for high-conviction positioning."

    def compute_watchlist_suggestion(self, thesis_key: str) -> str:
        key = str(thesis_key or "").lower()
        suggestions = {
            "energy": "Monitor Brent crude, XLE, USO",
            "oil": "Monitor Brent crude, WTI, energy ETFs",
            "gold": "Monitor XAU/USD, GLD, silver",
            "fed": "Monitor DXY, 2Y Treasury, SPY",
            "china": "Monitor USD/CNH, FXI, copper",
            "iran": "Monitor Brent crude, tanker stocks, USD/ILS",
            "sanction": "Monitor SWIFT alternatives, USD, affected EM FX",
            "recession": "Monitor yield curve, VIX, defensive sectors",
            "inflation": "Monitor TIPS, energy, materials, gold",
            "war": "Monitor VIX, gold, oil, defence stocks",
            "dollar": "Monitor DXY, EM FX basket, gold",
            "rate": "Monitor 2Y/10Y spread, financials, growth stocks",
        }
        for kw, suggestion in suggestions.items():
            if kw in key:
                return suggestion
        return "Monitor broad market sentiment and VIX"

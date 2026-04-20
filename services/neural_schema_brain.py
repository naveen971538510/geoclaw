import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from config import ENABLE_NEURAL_SCHEMA, NEURAL_SCHEMA_INTERVAL_SECONDS
from services.event_bus import publish
from services.ingest_service import run_ingestion_cycle
from services.llm_router import chat


class NeuralSchemaBrain:
    def __init__(self, interval_seconds: int = None):
        self.interval_seconds = max(2, int(interval_seconds or NEURAL_SCHEMA_INTERVAL_SECONDS or 8))
        self._scheduler = None
        self._latest_report = {}

    def start(self):
        if self._scheduler and self._scheduler.running:
            return False
        self._scheduler = BackgroundScheduler(timezone="UTC")
        self._scheduler.add_job(
            self.run_cycle,
            trigger="interval",
            seconds=self.interval_seconds,
            id="neural_schema_brain_cycle",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(timezone.utc),
        )
        self._scheduler.start()
        return True

    def stop(self):
        if not self._scheduler:
            return False
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._scheduler = None
        return True

    def get_latest_report(self):
        return dict(self._latest_report or {})

    def run_cycle(self):
        ingest = run_ingestion_cycle(max_records_per_source=2, enabled_sources=["rss", "social"], reasoning_budget=0)
        top = ingest.get("top", [])[:8]
        headlines = [str(item.get("headline") or "") for item in top if item.get("headline")]
        personas = ["Bull", "Bear", "Macro", "Risk", "Sentiment"]
        with ThreadPoolExecutor(max_workers=5) as pool:
            sims = list(pool.map(lambda p: self._simulate_agent(p, headlines), personas))
        weights = {"Bull": 1.2, "Bear": 1.0, "Macro": 1.1, "Risk": 1.3, "Sentiment": 0.9}
        net = sum((10 - s["score"]) * weights[s["persona"]] if s["persona"] == "Bear" else s["score"] * weights[s["persona"]] for s in sims)
        sensitivity = max(1, min(10, round(net / sum(weights.values()))))
        key_drivers = [f"{s['persona']}: {s['driver']}" for s in sorted(sims, key=lambda x: x["score"], reverse=True)[:3]]
        scenarios = self._build_scenarios(sensitivity, sims)
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "market_sensitivity_report": {
                "score": sensitivity,
                "emoji_bar": ("🟩" * max(1, sensitivity // 2)) + ("🟨" if sensitivity in {5, 6} else "") + ("🟥" * max(0, (10 - sensitivity) // 2)),
                "key_drivers": key_drivers,
            },
            "what_could_be_next": scenarios,
            "agents": sims,
            "ingest": {"items_fetched": ingest.get("items_fetched", 0), "items_kept": ingest.get("items_kept", 0)},
        }
        self._latest_report = report
        publish("neural_schema_update", report)
        return report

    def _simulate_agent(self, persona: str, headlines):
        prompt = f"You are {persona}. Return exactly: SCORE:<1-10>; DRIVER:<short>; SCENARIO:<short> from headlines: " + " | ".join(headlines[:6])
        try:
            resp = chat([{"role": "system", "content": "React fast with concise market reasoning."}, {"role": "user", "content": prompt}], timeout=8)
            text = str(((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        except Exception:
            text = ""
        score_match = re.search(r"SCORE\s*:\s*(10|[0-9])", text)
        driver_match = re.search(r"DRIVER\s*:\s*([^;\n]+)", text)
        scen_match = re.search(r"SCENARIO\s*:\s*([^;\n]+)", text)
        score = int(score_match.group(1)) if score_match else (7 if persona == "Bull" else 4 if persona == "Bear" else 5)
        return {
            "persona": persona,
            "score": max(1, min(10, score)),
            "driver": (driver_match.group(1).strip() if driver_match else "Mixed macro signals")[:80],
            "scenario": (scen_match.group(1).strip() if scen_match else "Range-bound with event risk")[:120],
        }

    def _build_scenarios(self, sensitivity: int, sims):
        base = [{"name": "Bull continuation", "p": 30 + sensitivity * 3}, {"name": "Range consolidation", "p": 40}, {"name": "Risk-off pullback", "p": 70 - sensitivity * 3}]
        total = sum(item["p"] for item in base) or 1
        bear = next((s for s in sims if s["persona"] == "Bear"), {"score": 5})
        bull = next((s for s in sims if s["persona"] == "Bull"), {"score": 5})
        return [
            {"scenario": base[0]["name"], "probability_pct": round(base[0]["p"] * 100 / total), "confidence": round((bull["score"] / 10) * 100)},
            {"scenario": base[1]["name"], "probability_pct": round(base[1]["p"] * 100 / total), "confidence": round((10 - abs(5 - sensitivity)) * 10)},
            {"scenario": base[2]["name"], "probability_pct": round(base[2]["p"] * 100 / total), "confidence": round((bear["score"] / 10) * 100)},
        ]


_brain = None


def get_neural_schema_brain():
    global _brain
    if _brain is None and ENABLE_NEURAL_SCHEMA:
        _brain = NeuralSchemaBrain()
    return _brain


def stop_neural_schema_brain():
    global _brain
    if _brain:
        _brain.stop()
        _brain = None

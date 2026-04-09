"""
GeoClaw scheduler loop:
- every 5 min: fetch prices (during market hours weekdays; BTC still attempted)
- every 1 hour: run signal engine
- every day 08:00 London weekdays: send morning briefing
Logs to logs/geoclaw.log
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import run_morning_briefing_job
from intelligence.signal_engine import run_signal_engine
from sources.price_fetcher import fetch_and_store_prices

LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "geoclaw.log"
LONDON = ZoneInfo("Europe/London")
NY = ZoneInfo("America/New_York")


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
    )


def _is_market_hours() -> bool:
    now = datetime.now(NY)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (t.hour > 9 or (t.hour == 9 and t.minute >= 30)) and (t.hour < 16 or (t.hour == 16 and t.minute == 0))


def main() -> None:
    _setup_logging()
    log = logging.getLogger("scheduler")
    log.info("scheduler started")
    last_price_min = None
    last_signal_hour = None
    last_brief_date = None

    while True:
        now_utc = datetime.utcnow()
        now_london = datetime.now(LONDON)

        # every 5 minutes during market hours
        minute_bucket = now_utc.minute // 5
        if _is_market_hours() and last_price_min != (now_utc.hour, minute_bucket):
            try:
                n = fetch_and_store_prices()
                log.info("price fetch cycle stored %s rows", n)
            except Exception:
                log.exception("price fetch cycle failed")
            last_price_min = (now_utc.hour, minute_bucket)

        # every hour
        if last_signal_hour != (now_utc.year, now_utc.month, now_utc.day, now_utc.hour):
            try:
                n = run_signal_engine()
                log.info("signal engine cycle wrote %s signals", n)
            except Exception:
                log.exception("signal engine cycle failed")
            last_signal_hour = (now_utc.year, now_utc.month, now_utc.day, now_utc.hour)

        # 08:00 London weekdays
        if now_london.weekday() < 5 and now_london.hour == 8 and now_london.minute < 5:
            date_key = now_london.date().isoformat()
            if last_brief_date != date_key:
                try:
                    run_morning_briefing_job(refresh_signals=False)
                    log.info("morning briefing sent")
                except Exception:
                    log.exception("morning briefing failed")
                last_brief_date = date_key

        time.sleep(20)


if __name__ == "__main__":
    main()

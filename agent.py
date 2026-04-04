"""
GeoClaw morning agent: every weekday 08:00 Europe/London sends a Groq-generated
briefing to Telegram and logs to logs/briefings.log.
"""

from __future__ import annotations

import html
import logging
import os
import sys
import time
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DB_PATH
from intelligence.db import ensure_intelligence_schema, get_database_url, query_all
from intelligence.groq_briefing import build_signals_context, generate_morning_briefing_block
from services.telegram_bot import TelegramBot

logger = logging.getLogger("geoclaw.agent")

LONDON = ZoneInfo("Europe/London")
LOG_DIR = ROOT / "logs"
BRIEFING_LOG = LOG_DIR / "briefings.log"


def _setup_briefing_log() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(BRIEFING_LOG, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    lg = logging.getLogger("briefings_file")
    lg.setLevel(logging.INFO)
    lg.handlers.clear()
    lg.addHandler(fh)
    lg.propagate = False


def seconds_until_next_london_weekday_8am() -> float:
    now = datetime.now(LONDON)
    for days_ahead in range(0, 12):
        day = (now + timedelta(days=days_ahead)).date()
        if day.weekday() >= 5:
            continue
        at8 = datetime.combine(day, time(8, 0), tzinfo=LONDON)
        if at8 > now:
            return max(30.0, (at8 - now).total_seconds())
    return 3600.0


def _gather_context() -> str:
    if not get_database_url():
        raise RuntimeError("DATABASE_URL is not set")
    ensure_intelligence_schema()
    since = datetime.now(timezone.utc) - timedelta(hours=48)
    signals = query_all(
        """
        SELECT signal_name, direction, confidence, explanation_plain_english, ts
        FROM geoclaw_signals WHERE ts >= %s ORDER BY confidence DESC LIMIT 25;
        """,
        (since,),
    )
    macro = query_all(
        """
        SELECT DISTINCT ON (metric_name)
            metric_name, value, previous_value, pct_change, observed_at
        FROM macro_signals
        ORDER BY metric_name, observed_at DESC;
        """
    )
    charts = query_all(
        """
        SELECT ticker, pattern_name, direction, confidence, detected_at
        FROM chart_signals
        ORDER BY detected_at DESC LIMIT 12;
        """
    )
    return build_signals_context(signals, macro, charts)


def _format_telegram_html(raw: str) -> str:
    lines = []
    lines.append("<b>══ GeoClaw Morning Briefing ══</b>")
    lines.append(f"<i>{html.escape(datetime.now(LONDON).strftime('%A %d %b %Y, %H:%M %Z'))}</i>\n")
    for block in raw.replace("\r\n", "\n").split("\n"):
        b = block.strip()
        if not b:
            continue
        if b.startswith("MACRO_OVERVIEW:"):
            lines.append("\n<b>📊 Macro overview</b>")
            lines.append(html.escape(b.split(":", 1)[1].strip()))
        elif b.startswith("TOP_SIGNALS:"):
            lines.append("\n<b>⚡ Top signals</b>")
            lines.append(html.escape(b.split(":", 1)[1].strip()))
        elif b.startswith("CHART_WATCH:"):
            lines.append("\n<b>📈 Chart watch</b>")
            lines.append(html.escape(b.split(":", 1)[1].strip()))
        elif b.startswith("MARKET_BIAS:"):
            lines.append("\n<b>🧭 Market bias</b>")
            lines.append(html.escape(b.split(":", 1)[1].strip()))
        else:
            lines.append(html.escape(b))
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3997] + "..."
    return text


def run_morning_briefing_job() -> None:
    bot = TelegramBot(str(DB_PATH))
    raw = generate_morning_briefing_block(_gather_context())
    ts = datetime.now(timezone.utc).isoformat()
    log = logging.getLogger("briefings_file")
    log.info("%s | %s", ts, raw.replace("\n", " | "))
    if not bot.available():
        logger.warning("Telegram not configured; briefing logged only")
        return
    html_msg = _format_telegram_html(raw)
    ok = bot.send_message(html_msg, parse_mode="HTML")
    if ok:
        logger.info("Morning briefing sent to Telegram")
    else:
        logger.warning("Telegram send failed")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _setup_briefing_log()
    logger.info("Agent started: weekday 08:00 London morning briefing + Groq + Telegram")

    while True:
        wait_s = seconds_until_next_london_weekday_8am()
        logger.info("Next London weekday 08:00 briefing in %.0f seconds", wait_s)
        time.sleep(wait_s)
        try:
            run_morning_briefing_job()
        except Exception:
            logger.exception("Morning briefing failed")
        time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Stopped")
        sys.exit(0)

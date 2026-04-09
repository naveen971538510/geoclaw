"""
GeoClaw morning agent:
- Refreshes signals first
- Sends BUY/SELL-only morning briefing to Telegram at 08:00 Europe/London weekdays
"""

from __future__ import annotations

import html
import logging
import os
import sys
import time
import warnings
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Silence urllib3's LibreSSL/OpenSSL warning in local macOS Python builds.
warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL.*")

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DB_PATH
from intelligence.db import ensure_intelligence_schema, get_database_url, query_all
from intelligence.signal_engine import run_signal_engine
from services.signal_taxonomy import SIGNAL_SECTION_ORDER, signal_asset_class
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


def _latest_actionable_signals(limit: int = 8):
    if not get_database_url():
        raise RuntimeError("DATABASE_URL is not set")
    ensure_intelligence_schema()
    since = datetime.now(timezone.utc) - timedelta(hours=48)
    return query_all(
        """
        SELECT signal_name, direction, confidence, explanation_plain_english, ts
        FROM geoclaw_signals
        WHERE ts >= %s
          AND direction IN ('BUY','SELL')
        ORDER BY confidence DESC, ts DESC
        LIMIT %s;
        """,
        (since, int(limit)),
    )


def _market_bias(rows):
    buy_score = sum(
        float(r.get("confidence") or 0.0)
        for r in rows
        if str(r.get("direction") or "").upper() == "BUY"
    )
    sell_score = sum(
        float(r.get("confidence") or 0.0)
        for r in rows
        if str(r.get("direction") or "").upper() == "SELL"
    )
    if buy_score > sell_score:
        return "BULLISH", buy_score, sell_score
    if sell_score > buy_score:
        return "BEARISH", buy_score, sell_score
    return "NEUTRAL", buy_score, sell_score

def _group_actionable_signals(rows):
    grouped = {section: [] for section in SIGNAL_SECTION_ORDER}
    for row in rows:
        grouped.setdefault(signal_asset_class(str(row.get("signal_name") or "")), []).append(row)
    return grouped


def _format_signal_brief_html(rows) -> str:
    bias, buy_score, sell_score = _market_bias(rows)
    lines = [
        "<b>══ GeoClaw Morning Briefing ══</b>",
        f"<i>{html.escape(datetime.now(LONDON).strftime('%A %d %b %Y, %H:%M %Z'))}</i>",
        "",
        f"<b>Market Bias:</b> {bias} (BUY={buy_score:.0f}%, SELL={sell_score:.0f}%)",
        "",
    ]
    if not rows:
        lines.append("No actionable BUY/SELL signals this morning.")
    else:
        lines.append("<b>Actionable Signals</b>")
        lines.append("")
        grouped = _group_actionable_signals(rows)
        for section in SIGNAL_SECTION_ORDER:
            section_rows = grouped.get(section) or []
            lines.append(f"<b>{html.escape(section)}</b>")
            if not section_rows:
                lines.append("No actionable signals.")
            else:
                for idx, r in enumerate(section_rows, start=1):
                    direction = str(r.get("direction") or "HOLD").upper()
                    emoji = "🟢" if direction == "BUY" else "🔴"
                    name = html.escape(str(r.get("signal_name") or ""))
                    conf = float(r.get("confidence") or 0.0)
                    lines.append(f"{idx}. {emoji} <b>{direction}</b> - <b>{name}</b> ({conf:.0f}%)")
            lines.append("")
    text = "\n".join(lines).strip()
    if len(text) > 4000:
        text = text[:3997] + "..."
    return text


def run_morning_briefing_job(refresh_signals: bool = True) -> None:
    bot = TelegramBot(str(DB_PATH))
    if refresh_signals:
        # run_signal_engine performs same-cycle (signal_name, direction) dedupe before inserts.
        run_signal_engine()
    else:
        logger.info("Skipping signal engine refresh inside morning briefing job; using current cycle output")
    rows = _latest_actionable_signals(limit=20)
    # Extra cycle dedupe guard in agent flow using a seen set on (signal_name, direction).
    seen = set()
    deduped_rows = []
    for r in rows:
        key = (str(r.get("signal_name") or ""), str(r.get("direction") or "").upper())
        if key in seen:
            continue
        seen.add(key)
        deduped_rows.append(r)
    rows = deduped_rows[:8]
    ts = datetime.now(timezone.utc).isoformat()
    log = logging.getLogger("briefings_file")
    log.info("%s | signals=%s", ts, len(rows))
    if not bot.available():
        logger.warning("Telegram not configured; briefing logged only")
        return
    html_msg = _format_signal_brief_html(rows)
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
    logger.info("Agent started: weekday 08:00 London morning briefing (BUY/SELL only)")

    while True:
        wait_s = seconds_until_next_london_weekday_8am()
        logger.info("Next London weekday 08:00 briefing in %.0f seconds", wait_s)
        time.sleep(int(wait_s))
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

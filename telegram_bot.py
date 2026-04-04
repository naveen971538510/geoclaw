"""
GeoClaw Telegram long-polling bot: natural language via /api/ask plus slash commands.
Uses HTML parse_mode for all replies. Requires TELEGRAM_BOT_TOKEN, DATABASE_URL for /signals /macro /charts.
"""

from __future__ import annotations

import html
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("geoclaw.telegram_polling")

DEFAULT_ASK_URL = "http://127.0.0.1:8000/api/ask"


def _api_base(token: str) -> str:
    return f"https://api.telegram.org/bot{token}"


def send_message(
    token: str,
    chat_id: Union[int, str],
    text: str,
    parse_mode: str = "HTML",
) -> bool:
    if not token or chat_id is None:
        return False
    try:
        payload = {
            "chat_id": chat_id,
            "text": str(text or "")[:4096],
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{_api_base(token)}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        if not data.get("ok"):
            logger.error("sendMessage API error: %s", data)
            return False
        return True
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        logger.error("sendMessage HTTP %s: %s", exc.code, body[:500])
        return False
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        logger.error("sendMessage failed: %s", exc)
        return False


def get_updates(token: str, offset: Optional[int]) -> List[dict]:
    if not token:
        return []
    params = [("timeout", "10")]
    if offset is not None:
        params.append(("offset", str(int(offset))))
    qs = urllib.parse.urlencode(params)
    url = f"{_api_base(token)}/getUpdates?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        if not data.get("ok"):
            logger.warning("getUpdates not ok: %s", data)
            return []
        return list(data.get("result") or [])
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning("getUpdates failed: %s", exc)
        return []


def ask_geoclaw(question: str, base_url: str) -> str:
    q = (question or "").strip()
    if not q:
        return "Send a non-empty question."
    try:
        qs = urllib.parse.urlencode({"q": q})
        url = f"{base_url}?{qs}" if "?" not in base_url else f"{base_url}&{qs}"
        with urllib.request.urlopen(url, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except urllib.error.HTTPError as exc:
        return f"Error reaching GeoClaw: HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return f"Error reaching GeoClaw: {exc}"

    if data.get("status") == "error":
        return f"Error: {data.get('error') or data.get('message') or data}"

    answer = data.get("answer") or data.get("result")
    if answer is not None:
        return str(answer)
    return str(data)


def _db_ok() -> bool:
    try:
        from intelligence.db import get_database_url

        return bool(get_database_url())
    except Exception:
        return False


def _emoji_dir(direction: str) -> str:
    d = (direction or "").upper()
    if d == "BEARISH":
        return "🔴"
    if d == "BULLISH":
        return "🟢"
    return "⚪"


def cmd_signals_html() -> str:
    from intelligence.db import query_all

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = query_all(
        """
        SELECT signal_name, direction, confidence, explanation_plain_english, ts
        FROM geoclaw_signals
        WHERE ts >= %s
        ORDER BY confidence DESC, ts DESC
        LIMIT 5;
        """,
        (since,),
    )
    if not rows:
        return "<b>Signals</b>\nNo scored signals in the last 24h yet."
    lines = ["<b>Top 5 signals</b> (by confidence)\n"]
    for r in rows:
        em = _emoji_dir(str(r.get("direction") or ""))
        name = html.escape(str(r.get("signal_name") or ""))
        conf = float(r.get("confidence") or 0)
        expl = html.escape(str(r.get("explanation_plain_english") or ""))
        ts = r.get("ts")
        ts_s = html.escape(ts.strftime("%Y-%m-%d %H:%M UTC") if ts else "")
        lines.append(f"{em} <b>{name}</b> — {conf:.0f}% conf\n<i>{expl}</i>\n{ts_s}\n")
    return "\n".join(lines)


def cmd_macro_html() -> str:
    from intelligence.db import query_all

    rows = query_all(
        """
        SELECT DISTINCT ON (metric_name)
            metric_name, value, previous_value, pct_change, observed_at
        FROM macro_signals
        ORDER BY metric_name, observed_at DESC;
        """
    )
    if not rows:
        return "<b>Macro</b>\nNo macro data ingested yet. Run <code>sources/macro_agent.py</code>."
    # Human labels
    labels = {
        "CPI_YOY_PCT": "CPI (YoY %)",
        "FEDFUNDS": "Fed funds rate (%)",
        "UNRATE": "Unemployment (%)",
        "GDP_GROWTH": "Real GDP growth (q/q %)",
        "TREASURY_10Y": "10Y Treasury (%)",
        "TREASURY_2Y": "2Y Treasury (%)",
        "NFP_LEVEL_THOUSANDS": "NFP level (000s)",
        "NFP_MOM_THOUSANDS": "NFP MoM (000s)",
    }
    lines = ["<b>Macro indicators</b>\n<pre>"]
    lines.append(f"{'Indicator':<28} {'Value':>12} {'Prev':>12} {'Δ%':>10}")
    lines.append("-" * 64)
    for r in rows:
        key = str(r.get("metric_name") or "")
        lab = labels.get(key, key)[:26]
        v = r.get("value")
        p = r.get("previous_value")
        ch = r.get("pct_change")
        v_s = f"{float(v):.3f}" if v is not None else "—"
        p_s = f"{float(p):.3f}" if p is not None else "—"
        c_s = f"{float(ch):.2f}" if ch is not None else "—"
        lines.append(f"{lab:<28} {v_s:>12} {p_s:>12} {c_s:>10}")
    lines.append("</pre>")
    return "\n".join(lines)


def cmd_charts_html() -> str:
    from intelligence.db import query_all

    rows = query_all(
        """
        SELECT ticker, pattern_name, direction, confidence, detected_at
        FROM chart_signals
        ORDER BY detected_at DESC
        LIMIT 15;
        """
    )
    if not rows:
        return "<b>Chart patterns</b>\nNo patterns stored yet. Run <code>intelligence/chart_agent.py</code>."
    lines = ["<b>Latest candlestick patterns</b>\n"]
    for r in rows:
        em = _emoji_dir(str(r.get("direction") or ""))
        t = html.escape(str(r.get("ticker") or ""))
        pn = html.escape(str(r.get("pattern_name") or ""))
        cf = float(r.get("confidence") or 0)
        dt = r.get("detected_at")
        ds = html.escape(dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "")
        lines.append(f"{em} <b>{t}</b> {pn} — {cf:.0f}% conf\n<i>{ds}</i>\n")
    return "\n".join(lines)


def cmd_briefing_html() -> str:
    from intelligence.db import query_all
    from intelligence.groq_briefing import build_signals_context, generate_dashboard_briefing

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
        ORDER BY detected_at DESC LIMIT 15;
        """
    )
    ctx = build_signals_context(signals, macro, charts)
    text = generate_dashboard_briefing(ctx)
    safe = html.escape(text)
    return f"<b>GeoClaw briefing</b>\n\n{safe}"


def cmd_help_html() -> str:
    return (
        "<b>GeoClaw bot commands</b>\n\n"
        "/signals — top 5 scored macro signals (24h)\n"
        "/macro — latest CPI, Fed, jobs, GDP, yields table\n"
        "/charts — recent candlestick patterns\n"
        "/briefing — AI market briefing (Groq)\n"
        "/help — this message\n\n"
        "Anything else is sent to GeoClaw <code>/api/ask</code>."
    )


def dispatch_command(text: str) -> Optional[str]:
    t = (text or "").strip()
    low = t.lower()
    if not low.startswith("/"):
        return None
    parts = low.split(maxsplit=1)
    cmd = parts[0]
    if cmd in ("/help", "/start"):
        return cmd_help_html()
    if not _db_ok():
        return (
            "<b>Database not configured</b>\nSet <code>DATABASE_URL</code> for /signals, /macro, /charts, /briefing."
        )
    try:
        from intelligence.db import ensure_intelligence_schema

        ensure_intelligence_schema()
    except Exception as exc:
        return f"<b>Database error</b>\n{html.escape(str(exc))}"
    if cmd == "/signals":
        try:
            return cmd_signals_html()
        except Exception as exc:
            return f"<b>Error</b> {html.escape(str(exc))}"
    if cmd == "/macro":
        try:
            return cmd_macro_html()
        except Exception as exc:
            return f"<b>Error</b> {html.escape(str(exc))}"
    if cmd == "/charts":
        try:
            return cmd_charts_html()
        except Exception as exc:
            return f"<b>Error</b> {html.escape(str(exc))}"
    if cmd == "/briefing":
        try:
            return cmd_briefing_html()
        except Exception as exc:
            return f"<b>Briefing error</b> {html.escape(str(exc))}"
    return None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is not set; exiting")
        raise SystemExit(1)

    base_url = (os.environ.get("GEOCLAW_API_ASK_URL") or DEFAULT_ASK_URL).strip()
    logger.info("Telegram polling bot starting; GeoClaw ask URL: %s", base_url)

    offset: Optional[int] = None
    while True:
        for update in get_updates(token, offset):
            offset = int(update["update_id"]) + 1
            msg = update.get("message") or {}
            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            text = (msg.get("text") or "").strip()
            if chat_id is None or not text:
                continue
            logger.info("Message from %s: %s", chat_id, text[:80])
            reply_html = dispatch_command(text)
            if reply_html is None:
                plain = ask_geoclaw(text, base_url)
                reply_html = html.escape(plain)
            send_message(token, chat_id, reply_html, parse_mode="HTML")
        time.sleep(1)


if __name__ == "__main__":
    main()

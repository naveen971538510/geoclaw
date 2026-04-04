"""
GeoClaw macro data agent: FRED + BLS → Postgres macro_signals.
Runs every 60 minutes; on error logs and retries after 5 minutes.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Repo root on path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intelligence.db import ensure_intelligence_schema, get_connection, get_database_url

LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "macro_agent.log"
INTERVAL_OK_SEC = 60 * 60
INTERVAL_ERR_SEC = 5 * 60

FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"
BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# FRED series IDs
SERIES = {
    "CPI_LEVEL": "CPIAUCSL",
    "FEDFUNDS": "FEDFUNDS",
    "UNRATE": "UNRATE",
    "GDP_GROWTH": "A191RL1Q225SBEA",  # Real GDP % change q/q
    "TREASURY_10Y": "DGS10",
    "TREASURY_2Y": "DGS2",
}

logger = logging.getLogger("macro_agent")


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == str(LOG_FILE) for h in root.handlers):
        root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)


def _fred_key() -> str:
    return (os.environ.get("FRED_API_KEY") or "").strip()


def fetch_fred_observations(series_id: str, limit: int = 24) -> List[Tuple[datetime, float]]:
    key = _fred_key()
    if not key:
        raise RuntimeError("FRED_API_KEY is not set")
    params = {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": str(limit),
    }
    url = f"{FRED_OBS_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=45) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    obs = data.get("observations") or []
    out: List[Tuple[datetime, float]] = []
    for row in obs:
        d_raw = row.get("date")
        v_raw = row.get("value")
        if not d_raw or v_raw in (".", None, ""):
            continue
        dt = datetime.strptime(d_raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        out.append((dt, float(v_raw)))
    return out


def _store_macro(
    metric_name: str,
    observed_at: datetime,
    value: float,
    previous_value: Optional[float],
    pct_change: Optional[float],
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    extra = extra or {}
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO macro_signals (metric_name, observed_at, value, previous_value, pct_change, extra)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (metric_name, observed_at)
            DO UPDATE SET
                value = EXCLUDED.value,
                previous_value = EXCLUDED.previous_value,
                pct_change = EXCLUDED.pct_change,
                extra = EXCLUDED.extra;
            """,
            (
                metric_name,
                observed_at,
                value,
                previous_value,
                pct_change,
                json.dumps(extra),
            ),
        )
        cur.close()


def ingest_fred_series(internal_name: str, fred_id: str) -> None:
    pts = fetch_fred_observations(fred_id, limit=3)
    if len(pts) < 1:
        return
    latest_dt, latest_v = pts[0]
    prev_v = pts[1][1] if len(pts) > 1 else None
    pct = None
    if prev_v is not None and prev_v != 0:
        pct = (latest_v - prev_v) / abs(prev_v) * 100.0
    _store_macro(internal_name, latest_dt, latest_v, prev_v, pct, {"fred_id": fred_id})


def ingest_cpi_yoy() -> None:
    """CPI year-over-year % change from CPIAUCSL."""
    pts = fetch_fred_observations(SERIES["CPI_LEVEL"], limit=14)
    if len(pts) < 13:
        return
    latest_dt, latest_v = pts[0]
    year_ago = pts[12][1]
    if year_ago == 0:
        return
    yoy = (latest_v - year_ago) / year_ago * 100.0
    prev_yoy = None
    if len(pts) >= 14:
        prev_level_now = pts[1][1]
        prev_level_yago = pts[13][1]
        if prev_level_yago:
            prev_yoy = (prev_level_now - prev_level_yago) / prev_level_yago * 100.0
    pct_delta = (yoy - prev_yoy) if prev_yoy is not None else None
    _store_macro(
        "CPI_YOY_PCT",
        latest_dt,
        yoy,
        prev_yoy,
        pct_delta,
        {"fred_id": SERIES["CPI_LEVEL"], "note": "year_over_year_percent"},
    )


def ingest_bls_nfp() -> None:
    """Latest nonfarm payrolls level and month-over-month change (thousands)."""
    now = datetime.now(timezone.utc)
    end_year = now.year
    start_year = end_year - 2
    body = json.dumps(
        {
            "seriesid": ["CES0000000001"],
            "startyear": str(start_year),
            "endyear": str(end_year),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        BLS_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS error: {payload}")
    series = (payload.get("Results") or {}).get("series") or []
    if not series:
        return
    data = series[0].get("data") or []
    # BLS returns newest first
    levels: List[Tuple[str, float]] = []
    for row in data:
        period = row.get("period")
        if period and period.startswith("M"):
            year = row.get("year")
            val = row.get("value")
            if year and val:
                try:
                    y = int(year)
                    mo = int(period.replace("M", ""))
                    label = f"{y}-{period}"
                    levels.append((label, y * 100 + mo, float(val.replace(",", ""))))
                except ValueError:
                    continue
    if len(levels) < 3:
        return
    levels.sort(key=lambda x: x[1], reverse=True)
    latest_label, _, latest_level = levels[0]
    _, _, prev_level = levels[1]
    _, _, prior_prior_level = levels[2]
    mom = latest_level - prev_level
    prior_mom = prev_level - prior_prior_level
    mom_pct = ((latest_level - prev_level) / prev_level * 100.0) if prev_level else None
    obs_date = datetime.now(timezone.utc)
    _store_macro(
        "NFP_LEVEL_THOUSANDS",
        obs_date,
        latest_level,
        prev_level,
        mom_pct,
        {"bls_period": latest_label, "mom_thousands": mom},
    )
    _store_macro(
        "NFP_MOM_THOUSANDS",
        obs_date,
        mom,
        prior_mom,
        ((mom - prior_mom) / abs(prior_mom) * 100.0) if prior_mom not in (None, 0) else None,
        {"bls_period": latest_label, "prior_mom_thousands": prior_mom},
    )


def run_ingestion_once() -> None:
    if not get_database_url():
        raise RuntimeError("DATABASE_URL is not set")
    ensure_intelligence_schema()
    if not _fred_key():
        raise RuntimeError("FRED_API_KEY is not set")
    ingest_cpi_yoy()
    for name, fid in SERIES.items():
        if name == "CPI_LEVEL":
            continue
        ingest_fred_series(name, fid)
    ingest_bls_nfp()
    # Run signal engine after fresh macro data
    from intelligence.signal_engine import run_signal_engine

    run_signal_engine()


def main() -> None:
    _setup_logging()
    logger.info("macro_agent starting (interval %ss, error retry %ss)", INTERVAL_OK_SEC, INTERVAL_ERR_SEC)
    while True:
        try:
            run_ingestion_once()
            logger.info("macro ingestion cycle complete")
            time.sleep(INTERVAL_OK_SEC)
        except Exception as exc:
            logger.exception("macro ingestion failed: %s", exc)
            time.sleep(INTERVAL_ERR_SEC)


if __name__ == "__main__":
    main()

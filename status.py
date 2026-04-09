from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intelligence.db import ensure_intelligence_schema, query_all, query_one


def _last_error_lines() -> int:
    log_file = ROOT / "logs" / "geoclaw.log"
    if not log_file.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    count = 0
    for line in log_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "ERROR" not in line and "failed" not in line.lower():
            continue
        try:
            ts = datetime.fromisoformat(line.split(" ", 1)[0])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                count += 1
        except Exception:
            count += 1
    return count


def main() -> None:
    ensure_intelligence_schema()
    last_signal = query_one(
        """
        SELECT signal_name, direction, confidence, ts
        FROM geoclaw_signals
        ORDER BY ts DESC
        LIMIT 1;
        """
    )
    last_prices = query_all(
        """
        SELECT DISTINCT ON (ticker) ticker, price, ts
        FROM price_data
        WHERE ticker IN ('BTCUSD','XAUUSD','SPX')
        ORDER BY ticker, ts DESC;
        """
    )
    last_tg = query_one(
        """
        SELECT signal_name, ts
        FROM geoclaw_signals
        ORDER BY ts DESC
        LIMIT 1;
        """
    )
    total_signals = query_one("SELECT COUNT(*) AS c FROM geoclaw_signals;")

    print("Last signal generated:")
    print(last_signal or {})
    print("\nLast price fetched (BTC, Gold, SPX):")
    print(last_prices)
    print("\nLast Telegram message sent (proxy time):")
    print({"time": (last_tg or {}).get("ts")})
    print("\nTotal signals in DB:")
    print((total_signals or {}).get("c", 0))
    print("\nAny errors in last 24h:")
    print(_last_error_lines())


if __name__ == "__main__":
    main()


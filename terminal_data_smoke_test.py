import json

from market import fetch_and_store_market_snapshots, get_latest_market_snapshots
from services.terminal_service import get_terminal_payload


def main():
    market_result = fetch_and_store_market_snapshots()
    print("market status:", market_result["status"])
    print("market saved:", market_result["saved"])
    if market_result["errors"]:
        print("market errors:")
        for err in market_result["errors"]:
            print("-", err)

    latest = get_latest_market_snapshots()
    print()
    print("latest market snapshots:", len(latest))
    for row in latest[:6]:
        print("-", row["symbol"], row["price"], row["change_pct"])

    payload = get_terminal_payload(limit=15)
    print()
    print("terminal stats:", json.dumps(payload["stats"], ensure_ascii=False))
    print("source distribution:")
    for row in payload["source_distribution"][:5]:
        print("-", row["source"], row["count"])

    print()
    print("top cards:")
    for i, card in enumerate(payload["cards"][:5], start=1):
        print(f'{i}. [{card["impact_score"]}] [{card["signal"]}] [{card["source"]}] {card["headline"]}')
        if card["asset_tags"]:
            print("   assets:", ", ".join(card["asset_tags"]))
        if card["alert_tags"]:
            print("   alerts:", ", ".join(card["alert_tags"]))
        if card["watchlist_hits"]:
            print("   watch:", ", ".join(card["watchlist_hits"]))

if __name__ == "__main__":
    main()

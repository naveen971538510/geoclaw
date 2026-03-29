from services import run_ingestion_cycle

def main():
    result = run_ingestion_cycle(max_records_per_source=15)

    print("status:", result["status"])
    print("items_fetched:", result["items_fetched"])
    print("items_kept:", result["items_kept"])
    print("alerts_created:", result["alerts_created"])

    if result["errors"]:
        print("errors:")
        for err in result["errors"]:
            print("-", err)

    print()
    print("top ranked articles:")
    for i, item in enumerate(result["top"], start=1):
        print(
            f"{i}. [{item['impact_score']}] [{item['priority']}] "
            f"[{item['signal']}] [{item['source_name']}] {item['headline']}"
        )
        if item["alert_tags"]:
            print("   alerts:", ", ".join(item["alert_tags"]))
        if item["asset_tags"]:
            print("   assets:", ", ".join(item["asset_tags"]))
        if item["watchlist_hits"]:
            print("   watch:", ", ".join(item["watchlist_hits"]))

if __name__ == "__main__":
    main()

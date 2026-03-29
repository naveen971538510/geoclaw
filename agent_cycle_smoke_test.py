from services.agent_service import run_agent_cycle, get_agent_status

def main():
    result = run_agent_cycle(max_records_per_source=8)

    print("status:", result["status"])
    print("topic_runs:", result["topic_runs"])
    print("items_fetched:", result["items_fetched"])
    print("items_kept:", result["items_kept"])
    print("alerts_created:", result["alerts_created"])

    if result["errors"]:
        print("errors:")
        for err in result["errors"][:10]:
            print("-", err)

    print()
    print("topics:")
    for row in result["topics"]:
        print(
            f"- {row['name']}: "
            f"status={row['status']} "
            f"fetched={row['items_fetched']} "
            f"kept={row['items_kept']} "
            f"alerts={row['alerts_created']} "
            f"sources={','.join(row.get('used_sources', []))}"
        )

    print()
    print("top preview:")
    for i, item in enumerate(result["top_preview"], start=1):
        print(
            f"{i}. [{item['impact_score']}] [{item['signal']}] "
            f"[{item['source']}] {item['headline']}"
        )
        if item["asset_tags"]:
            print("   assets:", ", ".join(item["asset_tags"]))
        if item["alert_tags"]:
            print("   alerts:", ", ".join(item["alert_tags"]))

    print()
    status = get_agent_status(limit=8)
    print("recent runs:", len(status["runs"]))
    print("terminal_stats:", status["terminal_stats"])
    print("market_count:", status["market_count"])
    print("top_alerts_count:", status["top_alerts_count"])
    print("gdelt_state:", status.get("gdelt_state", {}))

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""GeoClaw CLI — interact with the intelligence system from terminal."""

import argparse
import os
import sqlite3
import subprocess
import sys
from datetime import datetime


ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, os.environ.get("GEOCLAW_DB", "geoclaw.db"))


def _db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _divider(width=60):
    return "-" * width


def cmd_status(_args):
    conn = _db()
    thesis_count = conn.execute(
        "SELECT COUNT(*) FROM agent_theses WHERE COALESCE(status, '') != 'superseded'"
    ).fetchone()[0]
    article_count = conn.execute(
        "SELECT COUNT(*) FROM ingested_articles WHERE fetched_at >= datetime('now','-24 hours')"
    ).fetchone()[0]
    pending_actions = conn.execute(
        "SELECT COUNT(*) FROM agent_actions WHERE COALESCE(status, '') = 'pending'"
    ).fetchone()[0]
    last_run = conn.execute(
        "SELECT created_at FROM agent_journal ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    last_run_text = last_run[0][:19].replace("T", " ") if last_run and last_run[0] else "never"
    print("\nGeoClaw Status")
    print(_divider(40))
    print(f"  Active theses:    {thesis_count}")
    print(f"  Articles (24h):   {article_count}")
    print(f"  Pending actions:  {pending_actions}")
    print(f"  Last agent run:   {last_run_text}")
    print()


def cmd_theses(args):
    limit = max(1, int(args.limit or 10))
    conn = _db()
    rows = conn.execute(
        """
        SELECT thesis_key, confidence, status, terminal_risk
        FROM agent_theses
        WHERE COALESCE(status, '') != 'superseded'
        ORDER BY confidence DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()

    print(f"\nTop {limit} Theses")
    print(_divider(90))
    for row in rows:
        confidence = int(round(float(row["confidence"] or 0.0) * 100))
        bar = ("#" * (confidence // 10)).ljust(10, ".")
        status = str(row["status"] or "active")[:10]
        risk = str(row["terminal_risk"] or "LOW")[:8]
        key = str(row["thesis_key"] or "")[:70]
        print(f"  {confidence:3}% {bar}  [{status:10}] [{risk:8}]  {key}")
    print()


def cmd_brief(_args):
    conn = _db()
    row = conn.execute(
        "SELECT briefing_text, generated_at FROM agent_briefings ORDER BY generated_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        print("No briefing available. Run 'python3 geoclaw_cli.py run' first.")
        return
    generated_at = str(row["generated_at"] or "")[:19].replace("T", " ")
    print(f"\nGenerated: {generated_at} UTC\n")
    print(str(row["briefing_text"] or ""))


def cmd_run(_args):
    print("Starting agent run...")
    try:
        from services.agent_loop_service import run_real_agent_loop

        result = run_real_agent_loop(max_records_per_source=8)
        print("\nRun complete:")
        for key, value in result.items():
            if key == "steps":
                continue
            print(f"  {key}: {value}")
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)


def cmd_ingest(_args):
    print("Fetching news...")
    try:
        from services.feed_manager import FeedManager

        manager = FeedManager()
        articles = manager.fetch_all()
        saved = manager.save_to_db(articles, DB)
        print(f"Fetched {len(articles)} articles, saved {saved} new.")
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)


def cmd_ask(args):
    question = " ".join(args.question).strip()
    if not question:
        print("Question required.")
        sys.exit(1)
    try:
        from services.query_engine import QueryEngine

        engine = QueryEngine(DB)
        result = engine.ask(question)
        print(f"\nQ: {question}")
        print(_divider())
        print(f"A: {result.get('answer', '')}")
        print(f"\nConfidence: {round(float(result.get('confidence', 0.0) or 0.0) * 100)}%")
        follow_up = result.get("follow_up") or []
        if follow_up:
            print(f"\nFollow-up: {' | '.join(str(item) for item in follow_up)}")
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)


def cmd_prices(args):
    try:
        from services.price_feed import PriceFeed

        feed = PriceFeed()
        if not feed._available:
            print("yfinance not installed. Run: pip3 install yfinance")
            return
        snapshot = feed.get_snapshot(args.symbols if args.symbols else None)
        if not snapshot:
            print("No price data available.")
            return
        print(f"\nMarket Prices ({len(snapshot)} symbols)")
        print(_divider(56))
        for symbol, data in snapshot.items():
            price = data.get("price")
            change_pct = float(data.get("change_pct", 0.0) or 0.0)
            arrow = "▲" if change_pct > 0 else ("▼" if change_pct < 0 else "-")
            name = str(data.get("name") or "")[:20]
            price_text = f"{price:>10.2f}" if isinstance(price, (int, float)) else f"{'n/a':>10}"
            print(f"  {symbol:12} {price_text}  {arrow} {change_pct:+.2f}%  {name}")
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)


def cmd_export(args):
    from services.exporter import Exporter

    exporter = Exporter(DB)
    stem = args.output or f"geoclaw-export-{datetime.now().strftime('%Y%m%d-%H%M')}"
    if args.format == "theses":
        content = exporter.export_theses_csv()
        filename = stem + ".csv"
    elif args.format == "articles":
        content = exporter.export_articles_csv(7)
        filename = stem + ".csv"
    elif args.format == "briefing":
        content = exporter.export_briefing_txt()
        filename = stem + ".txt"
    else:
        content = exporter.export_full_json()
        filename = stem + ".json"

    with open(filename, "w", encoding="utf-8") as handle:
        handle.write(content)
    print(f"Exported to: {filename}")


def cmd_migrate(_args):
    print("Running migrations...")
    try:
        subprocess.run([sys.executable, os.path.join(ROOT, "migration.py")], check=True)
    except subprocess.CalledProcessError as exc:
        print(f"Migration failed: {exc}")
        sys.exit(exc.returncode or 1)
    print("Done.")


def build_parser():
    parser = argparse.ArgumentParser(prog="geoclaw", description="GeoClaw CLI")
    sub = parser.add_subparsers(title="commands", dest="command")

    p_status = sub.add_parser("status", help="Show agent status")
    p_status.set_defaults(func=cmd_status)

    p_theses = sub.add_parser("theses", help="Show top theses")
    p_theses.add_argument("-n", "--limit", type=int, default=10)
    p_theses.set_defaults(func=cmd_theses)

    p_brief = sub.add_parser("brief", help="Show latest briefing")
    p_brief.set_defaults(func=cmd_brief)

    p_run = sub.add_parser("run", help="Run agent once")
    p_run.set_defaults(func=cmd_run)

    p_ingest = sub.add_parser("ingest", help="Fetch news only")
    p_ingest.set_defaults(func=cmd_ingest)

    p_ask = sub.add_parser("ask", help="Ask GeoClaw a question")
    p_ask.add_argument("question", nargs="+")
    p_ask.set_defaults(func=cmd_ask)

    p_prices = sub.add_parser("prices", help="Show live market prices")
    p_prices.add_argument("symbols", nargs="*")
    p_prices.set_defaults(func=cmd_prices)

    p_export = sub.add_parser("export", help="Export data")
    p_export.add_argument("format", choices=["theses", "articles", "briefing", "json"])
    p_export.add_argument("-o", "--output")
    p_export.set_defaults(func=cmd_export)

    p_migrate = sub.add_parser("migrate", help="Run DB migrations")
    p_migrate.set_defaults(func=cmd_migrate)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
        return
    parser.print_help()


if __name__ == "__main__":
    main()

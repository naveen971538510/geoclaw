import os
from datetime import datetime, timedelta, timezone
from threading import Lock

from apscheduler.schedulers.background import BackgroundScheduler

from config import DB_PATH, SCHEDULER_INTERVAL_MINUTES
from market import fetch_and_store_market_snapshots
from services.agent_loop_service import run_real_agent_loop
from services.logging_service import get_logger


_scheduler = None
_lock = Lock()
_last_agent_run_at = ""
_last_market_run_at = ""
_last_briefing_run_at = ""
_last_agent_result = {}
_last_briefing_result = {}
_last_error = ""
_last_prices = {}
_interval_minutes = int(SCHEDULER_INTERVAL_MINUTES or 30)
PRICE_SPIKE_THRESHOLD_PCT = 2.0
logger = get_logger("scheduler")


def _agent_job():
    global _last_agent_run_at, _last_agent_result, _last_error
    try:
        result = run_real_agent_loop(max_records_per_source=8)
        _last_agent_run_at = datetime.now(timezone.utc).isoformat()
        _last_agent_result = {
            "status": result.get("status"),
            "decisions_created": result.get("decisions_created"),
            "tasks_created": result.get("tasks_created"),
            "evaluations_created": result.get("evaluations_created"),
            "alerts_created": result.get("alerts_created"),
        }
        _last_error = ""
        logger.info("agent_cycle_job complete: %s", _last_agent_result)

        # Apply thesis signals to portfolio after every agent run
        _apply_portfolio_signals()
    except Exception as exc:
        _last_error = str(exc)
        logger.warning("agent_cycle_job failed: %s", exc, exc_info=True)


def _apply_portfolio_signals():
    """Pull high-confidence theses and write position signals to portfolio_signals table."""
    try:
        from services.portfolio_service import PortfolioService
        from services.thesis_service import ThesisService
        svc = ThesisService(str(DB_PATH))
        theses = svc.get_theses(status="active", limit=50)
        portfolio = PortfolioService(str(DB_PATH))
        result = portfolio.apply_thesis_signals(
            theses,
            portfolio_value=float(os.environ.get("PORTFOLIO_VALUE_USD", "100000")),
            min_confidence=0.70,
            max_risk_pct=5.0,
            dry_run=False,
        )
        if result.get("applied", 0):
            logger.info("Portfolio signals recorded: %s new signals, %.1f%% total alloc",
                        result["applied"], result.get("total_alloc_pct", 0))
    except Exception as exc:
        logger.warning("_apply_portfolio_signals failed: %s", exc)


def _market_job():
    global _last_market_run_at, _last_error, _last_prices
    try:
        result = fetch_and_store_market_snapshots()
        _last_market_run_at = datetime.now(timezone.utc).isoformat()
        logger.info("market_snapshot_job complete: %s", result)

        _detect_price_spikes()
    except Exception as exc:
        _last_error = str(exc)
        logger.warning("market_snapshot_job failed: %s", exc, exc_info=True)


def _detect_price_spikes():
    global _last_prices
    try:
        from services.price_feed import PriceFeed
        feed = PriceFeed()
        snapshot = feed.get_snapshot()
        if not snapshot:
            return

        for symbol, data in snapshot.items():
            price = data.get("price")
            prev_price = _last_prices.get(symbol)
            if price is None or prev_price is None:
                if price is not None:
                    _last_prices[symbol] = price
                continue

            if prev_price > 0:
                pct_change = ((price - prev_price) / prev_price) * 100
                if abs(pct_change) >= PRICE_SPIKE_THRESHOLD_PCT:
                    from services.event_bus import publish
                    publish("price_spike", {
                        "ticker": symbol,
                        "symbol": symbol,
                        "name": data.get("name", symbol),
                        "price": price,
                        "previous_price": prev_price,
                        "pct_change": round(pct_change, 2),
                        "direction": "up" if pct_change > 0 else "down",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    logger.info("Price spike detected: %s %.2f%% (%s -> %s)", symbol, pct_change, prev_price, price)

            _last_prices[symbol] = price
    except Exception as exc:
        logger.debug("Price spike detection error: %s", exc)


def _briefing_job(audience: str = "trader", deliver: bool = False):
    global _last_briefing_run_at, _last_briefing_result, _last_error
    try:
        from services.alert_service import AlertService
        from services.briefing_service import generate_daily_briefing

        briefing = generate_daily_briefing(run_id=None, audience=audience, store=(audience == "trader"))
        text = str((briefing or {}).get("briefing_text", "") or "")
        sent = {"telegram": False, "email": False, "slack": False}

        if deliver and text:
            briefing_title = f"GeoClaw {audience.title()} Briefing"
            alerter = AlertService(str(DB_PATH))
            # Use send_briefing() which covers Slack + Email + Telegram in one call
            alerter.send_briefing(text, title=briefing_title)
            sent["telegram"] = True
            sent["email"] = bool(alerter.email_from and alerter.email_to)
            sent["slack"] = bool(alerter.slack_webhook)

        _last_briefing_run_at = datetime.now(timezone.utc).isoformat()
        _last_briefing_result = {
            "audience": audience,
            "generated_at": briefing.get("generated_at", ""),
            "stored": audience == "trader",
            "delivered": sent,
        }
        _last_error = ""
        logger.info("briefing_job complete: %s", _last_briefing_result)
    except Exception as exc:
        _last_error = str(exc)
        logger.warning("briefing_job failed: %s", exc, exc_info=True)


def _executive_briefing_job():
    _briefing_job(audience="executive", deliver=True)


def _trader_briefing_job():
    _briefing_job(audience="trader", deliver=False)


def _prediction_check_job():
    """Periodically check pending predictions against actual prices."""
    try:
        from services.prediction_tracker import PredictionTracker
        from services.event_bus import publish
        tracker = PredictionTracker(str(DB_PATH))
        results = tracker.check_pending_predictions()
        if results.get("checked", 0) > 0:
            logger.info("Prediction check: %s", results)
            if results.get("verified", 0) > 0 or results.get("refuted", 0) > 0:
                publish("prediction_checked", results)
    except Exception as exc:
        logger.debug("Prediction check failed: %s", exc)


def _build_scheduler(interval_minutes: int) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="UTC")
    next_agent = datetime.now(timezone.utc) + timedelta(minutes=max(1, int(interval_minutes)))
    sched.add_job(
        _agent_job,
        trigger="interval",
        minutes=max(1, int(interval_minutes)),
        id="geoclaw_agent_cycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=next_agent,
    )

    sched.add_job(
        _market_job,
        trigger="interval",
        minutes=5,
        id="geoclaw_market_snapshot",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    sched.add_job(
        _executive_briefing_job,
        trigger="cron",
        hour=7,
        minute=0,
        id="geoclaw_exec_briefing",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    sched.add_job(
        _trader_briefing_job,
        trigger="cron",
        hour=15,
        minute=0,
        id="geoclaw_trader_briefing",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    sched.add_job(
        _prediction_check_job,
        trigger="interval",
        hours=6,
        id="geoclaw_prediction_check",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    return sched


def start_scheduler(interval_minutes: int = None):
    global _scheduler, _interval_minutes

    with _lock:
        if _scheduler is not None and _scheduler.running:
            return False

        _interval_minutes = max(1, int(interval_minutes or SCHEDULER_INTERVAL_MINUTES or 30))
        sched = _build_scheduler(_interval_minutes)
        sched.start()
        _scheduler = sched
        logger.info("scheduler started at %s with interval=%s minutes", datetime.now(timezone.utc).isoformat(), _interval_minutes)

    try:
        from services.reactive_agent import start_reactive_agent
        start_reactive_agent()
        logger.info("Reactive agent started alongside scheduler")
    except Exception as exc:
        logger.warning("Could not start reactive agent: %s", exc)

    return True


def ensure_scheduler_started(interval_minutes: int = None):
    global _scheduler
    with _lock:
        if _scheduler is not None and _scheduler.running:
            return _scheduler
    start_scheduler(interval_minutes=interval_minutes)
    return _scheduler


def stop_scheduler():
    global _scheduler
    try:
        from services.reactive_agent import stop_reactive_agent
        stop_reactive_agent()
    except Exception:
        pass
    with _lock:
        if _scheduler is None:
            return False
        try:
            if _scheduler.running:
                _scheduler.shutdown(wait=False)
        finally:
            _scheduler = None
        logger.info("scheduler stopped at %s", datetime.now(timezone.utc).isoformat())
        return True


def get_scheduler_status():
    sched = _scheduler
    jobs = []
    if sched is not None:
        for job in sched.get_jobs():
            jobs.append(
                {
                    "id": job.id,
                    "next_run_time": str(job.next_run_time) if job.next_run_time else None,
                    "trigger": str(job.trigger),
                }
            )

    reactive_status = {}
    try:
        from services.reactive_agent import get_reactive_agent
        reactive_status = get_reactive_agent().get_status()
    except Exception:
        reactive_status = {"running": False}

    return {
        "running": bool(sched.running) if sched is not None else False,
        "job_count": len(jobs),
        "jobs": jobs,
        "interval_minutes": int(_interval_minutes or SCHEDULER_INTERVAL_MINUTES or 30),
        "last_agent_run_at": _last_agent_run_at,
        "last_market_run_at": _last_market_run_at,
        "last_briefing_run_at": _last_briefing_run_at,
        "last_agent_result": _last_agent_result,
        "last_briefing_result": _last_briefing_result,
        "last_error": _last_error,
        "reactive_agent": reactive_status,
    }


def scheduler_status() -> dict:
    return get_scheduler_status()

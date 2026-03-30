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
_interval_minutes = int(SCHEDULER_INTERVAL_MINUTES or 30)
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
    except Exception as exc:
        _last_error = str(exc)
        logger.warning("agent_cycle_job failed: %s", exc, exc_info=True)


def _market_job():
    global _last_market_run_at, _last_error
    try:
        result = fetch_and_store_market_snapshots()
        _last_market_run_at = datetime.now(timezone.utc).isoformat()
        logger.info("market_snapshot_job complete: %s", result)
    except Exception as exc:
        _last_error = str(exc)
        logger.warning("market_snapshot_job failed: %s", exc, exc_info=True)


def _briefing_job(audience: str = "trader", deliver: bool = False):
    global _last_briefing_run_at, _last_briefing_result, _last_error
    try:
        from services.alert_service import AlertService
        from services.briefing_service import generate_daily_briefing
        from services.telegram_bot import TelegramBot

        briefing = generate_daily_briefing(run_id=None, audience=audience, store=(audience == "trader"))
        text = str((briefing or {}).get("briefing_text", "") or "")
        sent = {"telegram": False, "email": False}

        if deliver and text:
            telegram = TelegramBot(str(DB_PATH))
            if telegram.available():
                sent["telegram"] = bool(telegram.send_message(text[:4000]))

            alerter = AlertService(str(DB_PATH))
            if alerter.email_from and alerter.email_to and alerter.email_pass:
                alerter._send_email("Executive Briefing", text)
                sent["email"] = True

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
    }


def scheduler_status() -> dict:
    return get_scheduler_status()

from datetime import datetime, timedelta, timezone
from threading import Lock

from apscheduler.schedulers.background import BackgroundScheduler

from market import fetch_and_store_market_snapshots
from services.agent_service import run_agent_cycle


_scheduler = None
_lock = Lock()


def _agent_job():
    try:
        result = run_agent_cycle(max_records_per_source=8)
        print("INFO: agent_cycle_job:", {
            "status": result.get("status"),
            "topic_runs": result.get("topic_runs"),
            "items_fetched": result.get("items_fetched"),
            "items_kept": result.get("items_kept"),
            "alerts_created": result.get("alerts_created"),
        })
    except Exception as exc:
        print("WARN: agent_cycle_job failed:", exc)


def _market_job():
    try:
        result = fetch_and_store_market_snapshots()
        print("INFO: market_snapshot_job:", result)
    except Exception as exc:
        print("WARN: market_snapshot_job failed:", exc)


def ensure_scheduler_started():
    global _scheduler

    with _lock:
        if _scheduler is not None and _scheduler.running:
            return _scheduler

        sched = BackgroundScheduler(timezone="UTC")

        sched.add_job(
            _agent_job,
            trigger="interval",
            minutes=15,
            id="geoclaw_agent_cycle",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(timezone.utc) + timedelta(minutes=15),
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

        sched.start()
        _scheduler = sched
        return _scheduler


def get_scheduler_status():
    sched = ensure_scheduler_started()
    jobs = []
    for job in sched.get_jobs():
        jobs.append(
            {
                "id": job.id,
                "next_run_time": str(job.next_run_time) if job.next_run_time else None,
                "trigger": str(job.trigger),
            }
        )

    return {
        "running": bool(sched.running),
        "job_count": len(jobs),
        "jobs": jobs,
    }

__all__ = [
    "run_ingestion_cycle",
    "run_agent_cycle",
    "get_agent_status",
    "ensure_scheduler_started",
    "get_scheduler_status",
]


def __getattr__(name):
    if name == "run_ingestion_cycle":
        from .ingest_service import run_ingestion_cycle

        return run_ingestion_cycle
    if name in {"run_agent_cycle", "get_agent_status"}:
        from .agent_service import run_agent_cycle, get_agent_status

        return {
            "run_agent_cycle": run_agent_cycle,
            "get_agent_status": get_agent_status,
        }[name]
    if name in {"ensure_scheduler_started", "get_scheduler_status"}:
        from .scheduler_service import ensure_scheduler_started, get_scheduler_status

        return {
            "ensure_scheduler_started": ensure_scheduler_started,
            "get_scheduler_status": get_scheduler_status,
        }[name]
    raise AttributeError(name)

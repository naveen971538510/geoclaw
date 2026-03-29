__all__ = [
    "fetch_and_store_market_snapshots",
    "get_latest_market_snapshots",
]


def __getattr__(name):
    if name in {"fetch_and_store_market_snapshots", "get_latest_market_snapshots"}:
        from .prices import fetch_and_store_market_snapshots, get_latest_market_snapshots

        return {
            "fetch_and_store_market_snapshots": fetch_and_store_market_snapshots,
            "get_latest_market_snapshots": get_latest_market_snapshots,
        }[name]
    raise AttributeError(name)

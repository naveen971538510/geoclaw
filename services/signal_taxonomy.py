from __future__ import annotations

from typing import Any, Dict, Iterable, List

SIGNAL_SECTION_ORDER = ("Rates & Inflation", "Labour & Growth", "Macro/Other")

_RATES_AND_INFLATION_TOKENS = (
    "federal reserve policy rate",
    "fed policy rate",
    "treasury yield curve",
    "consumer inflation",
    "cpi",
)

_LABOUR_AND_GROWTH_TOKENS = (
    "nonfarm payrolls",
    "unemployment rate",
    "gdp growth",
)


def signal_asset_class(signal_name: str) -> str:
    clean = str(signal_name or "").strip().lower()
    if any(token in clean for token in _RATES_AND_INFLATION_TOKENS):
        return "Rates & Inflation"
    if any(token in clean for token in _LABOUR_AND_GROWTH_TOKENS):
        return "Labour & Growth"
    return "Macro/Other"


def enrich_signal_row(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row or {})
    item["asset_class"] = signal_asset_class(str(item.get("signal_name") or ""))
    return item


def group_signals(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {section: [] for section in SIGNAL_SECTION_ORDER}
    for row in rows:
        item = enrich_signal_row(row)
        grouped.setdefault(str(item.get("asset_class") or "Macro/Other"), []).append(item)
    return grouped

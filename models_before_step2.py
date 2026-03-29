from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any


@dataclass
class RawArticle:
    source_name: str
    headline: str
    url: str
    published_at: str = ""
    summary: str = ""
    external_id: str = ""
    language: str = ""
    country: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedArticle:
    source_name: str
    headline: str
    url: str
    published_at: str = ""
    summary: str = ""
    external_id: str = ""
    language: str = ""
    country: str = ""
    fetched_at: str = ""
    content_hash: str = ""
    is_duplicate: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EnrichedArticle:
    article_id: int
    signal: str = "Neutral"
    sentiment_score: float = 0.0
    impact_score: int = 0
    asset_tags: List[str] = field(default_factory=list)
    macro_tags: List[str] = field(default_factory=list)
    watchlist_hits: List[str] = field(default_factory=list)
    alert_tags: List[str] = field(default_factory=list)
    thesis: str = ""
    bull_case: str = ""
    bear_case: str = ""
    what_to_watch: str = ""
    confidence: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MarketSnapshot:
    symbol: str
    label: str
    price: Optional[float] = None
    change_abs: Optional[float] = None
    change_pct: Optional[float] = None
    asof: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AlertEvent:
    article_id: int
    priority: str = "watch"
    reason: str = ""
    created_at: str = ""
    is_read: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

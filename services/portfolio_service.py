import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger("geoclaw.portfolio")


class PortfolioService:
    def __init__(self, db_path):
        self.db_path = db_path

    def add_position(
        self,
        symbol,
        name,
        asset_type,
        direction,
        quantity,
        entry_price,
        currency="USD",
        notes="",
        tags=None,
    ) -> int:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """
            INSERT INTO portfolio_positions
              (symbol, name, asset_type, direction, quantity, entry_price,
               currency, notes, added_at, updated_at, status, tags)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(symbol or "").strip().upper(),
                str(name or "").strip(),
                str(asset_type or "other").strip().lower(),
                str(direction or "long").strip().lower(),
                float(quantity or 0),
                float(entry_price or 0),
                str(currency or "USD").strip().upper(),
                str(notes or "").strip(),
                now,
                now,
                "open",
                json.dumps(tags or []),
            ),
        )
        position_id = int(cursor.lastrowid or 0)
        conn.commit()
        conn.close()
        return position_id

    def get_positions(self, status="open") -> list:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        if str(status or "open").lower() == "all":
            rows = conn.execute(
                "SELECT * FROM portfolio_positions ORDER BY updated_at DESC, added_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM portfolio_positions
                WHERE status=?
                ORDER BY updated_at DESC, added_at DESC
                """,
                (str(status or "open").lower(),),
            ).fetchall()
        conn.close()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["tags"] = json.loads(item.get("tags", "[]") or "[]")
            except Exception:
                item["tags"] = []
            items.append(item)
        return items

    def update_current_prices(self) -> dict:
        try:
            from services.price_feed import PriceFeed

            positions = self.get_positions("open")
            if not positions:
                return {"updated": 0}
            symbols = sorted({str(p.get("symbol") or "").strip().upper() for p in positions if p.get("symbol")})
            snapshot = PriceFeed().get_snapshot(symbols)
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            now = datetime.now(timezone.utc).isoformat()
            updated = 0
            for pos in positions:
                price_data = snapshot.get(str(pos.get("symbol") or "").strip().upper())
                if price_data and price_data.get("price") is not None:
                    conn.execute(
                        """
                        UPDATE portfolio_positions
                        SET current_price=?, updated_at=?
                        WHERE id=?
                        """,
                        (float(price_data["price"]), now, int(pos["id"])),
                    )
                    updated += 1
            conn.commit()
            conn.close()
            return {"updated": updated, "symbols": symbols}
        except Exception as exc:
            return {"error": str(exc), "updated": 0}

    def get_pnl_summary(self) -> dict:
        positions = self.get_positions("open")
        total_value = 0.0
        total_cost = 0.0
        total_pnl = 0.0
        enriched_positions = []

        for pos in positions:
            entry = float(pos.get("entry_price") or 0.0)
            current = float(pos.get("current_price") or entry or 0.0)
            qty = float(pos.get("quantity") or 0.0)
            direction = str(pos.get("direction") or "long").lower()
            cost = entry * qty
            value = current * qty
            pnl = (entry - current) * qty if direction == "short" else (current - entry) * qty
            pnl_pct = (pnl / cost * 100.0) if cost else 0.0

            total_value += value
            total_cost += cost
            total_pnl += pnl
            enriched_positions.append(
                {
                    **pos,
                    "entry_price": round(entry, 4),
                    "current_price": round(current, 4),
                    "quantity": qty,
                    "cost": round(cost, 2),
                    "value": round(value, 2),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                }
            )

        total_pnl_pct = (total_pnl / total_cost * 100.0) if total_cost else 0.0
        return {
            "total_value": round(total_value, 2),
            "total_cost": round(total_cost, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "positions_count": len(enriched_positions),
            "positions": enriched_positions,
        }

    def get_thesis_threats(self) -> list:
        positions = self.get_positions("open")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        threats = []
        seen = set()
        for pos in positions:
            tags = pos.get("tags") if isinstance(pos.get("tags"), list) else []
            if not tags:
                tags = [
                    str(pos.get("symbol") or "").lower(),
                    str(pos.get("asset_type") or "").lower(),
                    str(pos.get("name") or "").lower(),
                ]
            for tag in [str(tag or "").strip().lower() for tag in tags if str(tag or "").strip()][:3]:
                rows = conn.execute(
                    """
                    SELECT thesis_key, confidence, terminal_risk, watchlist_suggestion
                    FROM agent_theses
                    WHERE thesis_key LIKE ?
                      AND terminal_risk='HIGH'
                      AND COALESCE(status, '') != 'superseded'
                      AND confidence >= 0.65
                    ORDER BY confidence DESC
                    LIMIT 2
                    """,
                    (f"%{tag}%",),
                ).fetchall()
                for row in rows:
                    key = (int(pos.get("id") or 0), str(row["thesis_key"] or ""))
                    if key in seen:
                        continue
                    seen.add(key)
                    threats.append(
                        {
                            "position_id": int(pos.get("id") or 0),
                            "position_symbol": str(pos.get("symbol") or ""),
                            "position_name": str(pos.get("name") or ""),
                            "position_direction": str(pos.get("direction") or "long"),
                            "threat_thesis": str(row["thesis_key"] or "")[:140],
                            "threat_confidence": float(row["confidence"] or 0.0),
                            "terminal_risk": str(row["terminal_risk"] or ""),
                            "watchlist_suggestion": str(row["watchlist_suggestion"] or ""),
                        }
                    )
        conn.close()
        threats.sort(key=lambda item: float(item.get("threat_confidence", 0.0) or 0.0), reverse=True)
        return threats

    # ── Thesis → Position Sizing ────────────────────────────────────────

    _DIRECTION_KEYWORDS = {
        "long": ["bullish", "buy", "long", "upside", "rally", "surge", "rise", "support"],
        "short": ["bearish", "sell", "short", "downside", "fall", "drop", "decline", "pressure"],
    }

    @staticmethod
    def _thesis_direction(thesis: dict) -> str:
        """Infer long/short from thesis key text."""
        text = (str(thesis.get("thesis_key", "") or "")).lower()
        long_hits = sum(1 for w in PortfolioService._DIRECTION_KEYWORDS["long"] if w in text)
        short_hits = sum(1 for w in PortfolioService._DIRECTION_KEYWORDS["short"] if w in text)
        return "short" if short_hits > long_hits else "long"

    @staticmethod
    def _thesis_symbol(thesis: dict) -> str:
        """Best-effort symbol extraction from thesis key."""
        suggestion = str(thesis.get("watchlist_suggestion") or "").strip().upper()
        if suggestion and 1 <= len(suggestion) <= 6 and suggestion.isalpha():
            return suggestion
        text = (str(thesis.get("thesis_key") or "")).upper()
        # common asset keywords → symbols
        MAP = {
            "GOLD": "GLD", "OIL": "USO", "CRUDE": "USO",
            "S&P": "SPY", "S&P500": "SPY", "NASDAQ": "QQQ",
            "BITCOIN": "BTC-USD", "CRYPTO": "BTC-USD",
            "DOLLAR": "UUP", "USD": "UUP",
            "BONDS": "TLT", "TREASURY": "TLT",
            "COPPER": "CPER", "SILVER": "SLV",
        }
        for kw, sym in MAP.items():
            if kw in text:
                return sym
        return "MULTI"

    @staticmethod
    def _size_from_confidence(confidence: float, max_risk_pct: float = 5.0) -> float:
        """
        Kelly-lite sizing: allocate 0–max_risk_pct of portfolio based on confidence.
        confidence=0.65 → ~1.5%,  confidence=0.80 → ~3.5%,  confidence=0.95 → max_risk_pct
        Formula: allocation = max_risk_pct * ((confidence - 0.65) / 0.35) ** 0.7
        """
        conf = max(0.65, min(1.0, float(confidence or 0)))
        raw = max_risk_pct * ((conf - 0.65) / 0.35) ** 0.7
        return round(min(raw, max_risk_pct), 2)

    def suggest_from_thesis(self, thesis: dict, portfolio_value: float = 100_000.0,
                            max_risk_pct: float = 5.0) -> dict:
        """
        Compute a position suggestion from a single thesis.
        Does NOT write to DB — returns a suggestion dict for the caller to approve.
        """
        confidence = float(thesis.get("confidence") or 0)
        if confidence < 0.65:
            return {}
        symbol = self._thesis_symbol(thesis)
        direction = self._thesis_direction(thesis)
        alloc_pct = self._size_from_confidence(confidence, max_risk_pct)
        alloc_usd = round(portfolio_value * alloc_pct / 100, 2)
        return {
            "symbol": symbol,
            "direction": direction,
            "confidence": round(confidence * 100, 1),
            "alloc_pct": alloc_pct,
            "alloc_usd": alloc_usd,
            "thesis_key": str(thesis.get("thesis_key") or "")[:200],
            "terminal_risk": str(thesis.get("terminal_risk") or "MEDIUM"),
            "suggested_at": datetime.now(timezone.utc).isoformat(),
        }

    def apply_thesis_signals(self, theses: list, portfolio_value: float = 100_000.0,
                             min_confidence: float = 0.70, max_risk_pct: float = 5.0,
                             dry_run: bool = True) -> dict:
        """
        Process high-confidence theses and either suggest or record positions.

        Args:
            theses: list of thesis dicts from thesis_service
            portfolio_value: total portfolio size in USD (used for sizing)
            min_confidence: minimum confidence to consider (default 70%)
            max_risk_pct: maximum allocation per thesis as % of portfolio (default 5%)
            dry_run: if True, returns suggestions without writing to DB.
                     if False, records suggestions to portfolio_signals table.

        Returns:
            dict with 'suggestions' list and 'applied' count
        """
        eligible = [t for t in (theses or []) if float(t.get("confidence") or 0) >= min_confidence
                    and str(t.get("status") or "active").lower() not in ("superseded", "stale")]
        suggestions = []
        for thesis in eligible:
            suggestion = self.suggest_from_thesis(thesis, portfolio_value, max_risk_pct)
            if suggestion:
                suggestions.append(suggestion)

        applied = 0
        if not dry_run and suggestions:
            applied = self._record_signals(suggestions)

        total_alloc_pct = round(sum(s.get("alloc_pct", 0) for s in suggestions), 2)
        logger.info("apply_thesis_signals: %d eligible, %d suggestions, %.1f%% total alloc",
                    len(eligible), len(suggestions), total_alloc_pct)
        return {
            "eligible_theses": len(eligible),
            "suggestions": suggestions,
            "total_alloc_pct": total_alloc_pct,
            "total_alloc_usd": round(portfolio_value * total_alloc_pct / 100, 2),
            "applied": applied,
            "dry_run": dry_run,
        }

    def _record_signals(self, suggestions: list) -> int:
        """Persist position signals to portfolio_signals table (created if missing)."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    direction TEXT,
                    confidence REAL,
                    alloc_pct REAL,
                    alloc_usd REAL,
                    thesis_key TEXT,
                    terminal_risk TEXT,
                    status TEXT DEFAULT 'pending',
                    suggested_at TEXT,
                    actioned_at TEXT
                )
            """)
            now = datetime.now(timezone.utc).isoformat()
            count = 0
            for s in suggestions:
                # Skip if a pending signal for same symbol+direction already exists
                existing = conn.execute(
                    "SELECT id FROM portfolio_signals WHERE symbol=? AND direction=? AND status='pending' LIMIT 1",
                    (s.get("symbol", ""), s.get("direction", "long"))
                ).fetchone()
                if existing:
                    continue
                conn.execute("""
                    INSERT INTO portfolio_signals
                        (symbol, direction, confidence, alloc_pct, alloc_usd,
                         thesis_key, terminal_risk, suggested_at)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (
                    s.get("symbol", ""), s.get("direction", "long"),
                    s.get("confidence", 0), s.get("alloc_pct", 0), s.get("alloc_usd", 0),
                    s.get("thesis_key", "")[:300], s.get("terminal_risk", "MEDIUM"), now,
                ))
                count += 1
            conn.commit()
            conn.close()
            return count
        except Exception as exc:
            logger.error("_record_signals failed: %s", exc)
            return 0

    def get_pending_signals(self) -> list:
        """Return all pending position signals for operator review."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT, direction TEXT, confidence REAL, alloc_pct REAL,
                    alloc_usd REAL, thesis_key TEXT, terminal_risk TEXT,
                    status TEXT DEFAULT 'pending', suggested_at TEXT, actioned_at TEXT
                )
            """)
            rows = conn.execute(
                "SELECT * FROM portfolio_signals WHERE status='pending' ORDER BY confidence DESC, suggested_at DESC"
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.error("get_pending_signals: %s", exc)
            return []

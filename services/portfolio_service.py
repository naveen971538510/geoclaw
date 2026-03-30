import json
import sqlite3
from datetime import datetime, timezone


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

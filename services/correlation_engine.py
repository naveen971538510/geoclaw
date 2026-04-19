import math
import sqlite3


class CorrelationEngine:
    def _pearson(self, x: list, y: list) -> float:
        n = len(x)
        if n < 3:
            return 0.0
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        numerator = sum((left - mean_x) * (right - mean_y) for left, right in zip(x, y, strict=False))
        denom_x = math.sqrt(sum((value - mean_x) ** 2 for value in x))
        denom_y = math.sqrt(sum((value - mean_y) ** 2 for value in y))
        return numerator / (denom_x * denom_y) if denom_x and denom_y else 0.0

    def compute_correlations(self, db_path: str, symbols: list = None, hours: int = 48) -> dict:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        if not symbols:
            symbols = ["GC=F", "CL=F", "^VIX", "DX-Y.NYB", "^TNX", "SPY", "EEM", "EURUSD=X"]

        price_series = {}
        for symbol in symbols:
            rows = conn.execute(
                """
                SELECT change_pct, captured_at
                FROM price_snapshots
                WHERE symbol = ? AND captured_at >= datetime('now', ? || ' hours')
                ORDER BY captured_at ASC
                """,
                (symbol, f"-{int(hours or 48)}"),
            ).fetchall()
            if len(rows) >= 3:
                price_series[symbol] = [float(row["change_pct"] or 0.0) for row in rows]
        conn.close()

        if len(price_series) < 2:
            return {"error": "Insufficient price history", "correlations": {}}

        min_len = min(len(values) for values in price_series.values())
        aligned = {key: values[-min_len:] for key, values in price_series.items()}

        matrix = {}
        symbols_list = list(aligned.keys())
        for left in symbols_list:
            matrix[left] = {}
            for right in symbols_list:
                if left == right:
                    matrix[left][right] = 1.0
                else:
                    matrix[left][right] = round(self._pearson(aligned[left], aligned[right]), 3)

        strong_pairs = []
        for index, left in enumerate(symbols_list):
            for right in symbols_list[index + 1 :]:
                corr = matrix[left][right]
                if abs(corr) >= 0.6:
                    strong_pairs.append(
                        {
                            "asset_a": left,
                            "asset_b": right,
                            "correlation": corr,
                            "type": "positive" if corr > 0 else "negative",
                            "strength": "strong" if abs(corr) > 0.8 else "moderate",
                        }
                    )
        strong_pairs.sort(key=lambda item: abs(item["correlation"]), reverse=True)

        return {
            "matrix": matrix,
            "strong_pairs": strong_pairs[:10],
            "symbols": symbols_list,
            "data_points": min_len,
            "hours_covered": int(hours or 48),
        }

    def get_thesis_correlation_insight(self, thesis_key: str, correlations: dict) -> str:
        key = str(thesis_key or "").lower()
        insights = []
        for pair in correlations.get("strong_pairs", []):
            if pair["asset_a"] in {"GC=F", "^VIX"} or pair["asset_b"] in {"GC=F", "^VIX"}:
                if "oil" in key or "iran" in key or "war" in key:
                    corr = pair["correlation"]
                    left = pair["asset_a"]
                    right = pair["asset_b"]
                    if corr > 0.7:
                        insights.append(f"{left} and {right} are moving together ({corr:.0%}) — confirms risk-off correlation.")
                    elif corr < -0.7:
                        insights.append(f"{left} and {right} are moving oppositely ({corr:.0%}) — unusual divergence.")
        return " ".join(insights[:2]) if insights else "Correlation data building — run more agent cycles."

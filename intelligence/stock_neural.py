"""
Stock Neural Predictor
======================
Lightweight MLP that predicts 24h price direction (UP/DOWN) probability.

Feature vector per day (12 features):
  RSI, MACD histogram, Bollinger %B, volume ratio,
  price momentum 3d/5d/10d, ATR ratio, candle body ratio,
  upper/lower wick ratio, day-of-week, sentiment_score (optional)

Train: 1 year daily OHLCV → labels (next-day close > today close)
Infer: today's features → P(UP) 0-100

Models are cached in .state/neural/ and retrained weekly.
"""

from __future__ import annotations

import json
import logging
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.model_selection import cross_val_score
from ta.momentum import RSIIndicator
from ta.trend import MACD
from ta.volatility import BollingerBands, AverageTrueRange

logger = logging.getLogger("geoclaw.neural")

MODEL_DIR = Path(__file__).resolve().parents[1] / ".state" / "neural"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

RETRAIN_DAYS = 7   # retrain model if older than this


# ─── Feature Engineering ──────────────────────────────────────────────────────

def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build 12-feature matrix from OHLCV dataframe."""
    close = df["Close"]
    high, low, vol = df["High"], df["Low"], df["Volume"]

    # Momentum indicators
    rsi  = RSIIndicator(close=close, window=14).rsi()
    macd = MACD(close=close).macd_diff()
    bb   = BollingerBands(close=close, window=20)
    bb_pct = (close - bb.bollinger_lband()) / (bb.bollinger_hband() - bb.bollinger_lband() + 1e-9)
    atr  = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()

    # Price momentum
    mom3  = close.pct_change(3)
    mom5  = close.pct_change(5)
    mom10 = close.pct_change(10)

    # Volume ratio vs 20d avg
    vol_ratio = vol / vol.rolling(20).mean()

    # Candle structure
    body  = (close - df["Open"]).abs() / (high - low + 1e-9)
    upper_wick = (high - close.combine(df["Open"], max)) / (high - low + 1e-9)
    lower_wick = (close.combine(df["Open"], min) - low) / (high - low + 1e-9)

    # ATR ratio (normalised volatility)
    atr_ratio = atr / close

    # Day of week (0=Mon, 4=Fri)
    dow = pd.Series(df.index.dayofweek, index=df.index, dtype=float)

    feat = pd.DataFrame({
        "rsi": rsi, "macd_hist": macd, "bb_pct": bb_pct,
        "vol_ratio": vol_ratio, "mom3": mom3, "mom5": mom5, "mom10": mom10,
        "atr_ratio": atr_ratio, "body": body,
        "upper_wick": upper_wick, "lower_wick": lower_wick, "dow": dow,
    })
    return feat.replace([np.inf, -np.inf], np.nan)


# ─── Training ─────────────────────────────────────────────────────────────────

def _train(ticker: str) -> Pipeline:
    logger.info("Training neural model for %s", ticker)
    df = yf.Ticker(ticker).history(period="2y", interval="1d", auto_adjust=True)
    if df is None or len(df) < 60:
        raise RuntimeError(f"Insufficient data for {ticker}")

    feat = _build_features(df)
    label = (df["Close"].shift(-1) > df["Close"]).astype(int)  # 1=UP, 0=DOWN

    combined = feat.copy()
    combined["label"] = label
    combined = combined.dropna()

    X = combined.drop("label", axis=1).values
    y = combined["label"].values

    if len(X) < 40:
        raise RuntimeError(f"Not enough clean rows for {ticker}")

    model = Pipeline([
        ("scaler", StandardScaler()),
        ("mlp", MLPClassifier(
            hidden_layer_sizes=(64, 32, 16),
            activation="relu",
            max_iter=500,
            random_state=42,
            early_stopping=True,
            n_iter_no_change=20,
        )),
    ])
    model.fit(X, y)

    # Quick cross-val accuracy
    scores = cross_val_score(model, X, y, cv=5, scoring="accuracy")
    accuracy = round(float(scores.mean()) * 100, 1)
    logger.info("%s model accuracy: %.1f%%", ticker, accuracy)

    return model, accuracy


def _model_path(ticker: str) -> Path:
    return MODEL_DIR / f"{ticker}.pkl"


def _meta_path(ticker: str) -> Path:
    return MODEL_DIR / f"{ticker}.meta.json"


def _load_or_train(ticker: str) -> tuple[Pipeline, dict]:
    mp = _model_path(ticker)
    meta_p = _meta_path(ticker)

    # Load if fresh enough
    if mp.exists() and meta_p.exists():
        meta = json.loads(meta_p.read_text())
        trained_at = datetime.fromisoformat(meta["trained_at"])
        age_days = (datetime.now(timezone.utc) - trained_at).days
        if age_days < RETRAIN_DAYS:
            with open(mp, "rb") as f:
                model = pickle.load(f)
            return model, meta

    # Train fresh
    model, accuracy = _train(ticker)
    meta = {
        "ticker": ticker,
        "accuracy": accuracy,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(mp, "wb") as f:
        pickle.dump(model, f)
    meta_p.write_text(json.dumps(meta))
    return model, meta


# ─── Inference ────────────────────────────────────────────────────────────────

def predict(ticker: str, sentiment_score: Optional[float] = None) -> Dict[str, Any]:
    """
    Predict 24h direction for ticker.
    Returns: probability_up (0-100), direction, confidence, model_accuracy.
    """
    ticker = ticker.upper()
    try:
        model, meta = _load_or_train(ticker)

        df = yf.Ticker(ticker).history(period="3mo", interval="1d", auto_adjust=True)
        if df is None or len(df) < 30:
            raise RuntimeError("Insufficient recent data")

        feat = _build_features(df).dropna()
        if feat.empty:
            raise RuntimeError("Feature extraction failed")

        latest = feat.iloc[[-1]].values
        proba = model.predict_proba(latest)[0]  # [P(DOWN), P(UP)]
        prob_up = round(float(proba[1]) * 100, 1)

        # Optionally blend with sentiment (10% weight if provided)
        if sentiment_score is not None:
            prob_up = round(prob_up * 0.90 + sentiment_score * 0.10, 1)
            prob_up = min(99.0, max(1.0, prob_up))

        if prob_up >= 65:
            direction, conf = "UP", "HIGH"
        elif prob_up >= 55:
            direction, conf = "UP", "MODERATE"
        elif prob_up <= 35:
            direction, conf = "DOWN", "HIGH"
        elif prob_up <= 45:
            direction, conf = "DOWN", "MODERATE"
        else:
            direction, conf = "NEUTRAL", "LOW"

        return {
            "ticker": ticker,
            "probability_up": prob_up,
            "probability_down": round(100 - prob_up, 1),
            "direction": direction,
            "confidence": conf,
            "model_accuracy": meta.get("accuracy"),
            "model_trained_at": meta.get("trained_at"),
            "predicted_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as exc:
        logger.warning("neural predict failed for %s: %s", ticker, exc)
        return {"ticker": ticker, "error": str(exc), "probability_up": None}

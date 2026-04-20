"""
Hedge Fund Router
=================
6 investor persona agents that analyse a ticker using yfinance fundamentals
and generate LLM-backed signals (bullish / bearish / neutral) with reasoning.

Agents:
  warren_buffett   — Value, moat, FCF, margin of safety
  michael_burry    — Deep value, contrarian, FCF yield
  cathie_wood      — Growth, disruption, R&D intensity
  charlie_munger   — Quality, ROIC, durable competitive advantage
  nassim_taleb     — Tail risk, leverage, black-swan exposure
  technicals       — RSI / MACD / Bollinger from GeoClaw quant engine

Usage:
    from services.hedge_fund_router import run_hedge_fund_analysis
    result = run_hedge_fund_analysis("AAPL")
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yfinance as yf

logger = logging.getLogger("geoclaw.hedgefund")

# ─── LLM helper ──────────────────────────────────────────────────────────────

def _call_openai(system: str, user: str, model: str = "gpt-4o-mini") -> str:
    """Call OpenAI chat completion. Returns the text response or raises."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0.3,
            max_tokens=300,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("OpenAI call failed: %s", exc)
        return ""


def _parse_llm_signal(text: str) -> tuple[str, float]:
    """Extract signal and confidence from LLM output text."""
    import re
    t = text.lower()
    if "bullish" in t:
        signal = "bullish"
    elif "bearish" in t:
        signal = "bearish"
    else:
        signal = "neutral"
    m = re.search(r"confidence[:\s]+(\d+)", t)
    confidence = float(m.group(1)) if m else 60.0
    confidence = max(0.0, min(100.0, confidence))
    return signal, confidence


# ─── Fundamental data loader ──────────────────────────────────────────────────

def _get_fundamentals(ticker: str) -> Dict[str, Any]:
    """
    Fetch fundamentals via yfinance. Returns a flat dict of key metrics.
    Values are None when unavailable.
    """
    t = yf.Ticker(ticker)
    info = t.info or {}

    def _safe(key, default=None):
        v = info.get(key)
        return v if v not in (None, "N/A", "None") else default

    # Income statement / cash flow (annual)
    try:
        fin = t.financials  # columns = periods (newest first)
        cf  = t.cashflow
        bs  = t.balance_sheet
    except Exception:
        fin = cf = bs = None

    def _row(df, *names):
        if df is None or df.empty:
            return None
        for n in names:
            if n in df.index:
                vals = df.loc[n].dropna()
                return list(vals.values) if not vals.empty else None
        return None

    revenue_series   = _row(fin, "Total Revenue")
    gross_profit_s   = _row(fin, "Gross Profit")
    net_income_s     = _row(fin, "Net Income")
    rd_series        = _row(fin, "Research Development", "Research And Development")
    fcf_series       = _row(cf,  "Free Cash Flow")
    capex_series     = _row(cf,  "Capital Expenditure", "Capital Expenditures")
    total_debt_s     = _row(bs,  "Total Debt", "Long Term Debt")
    cash_s           = _row(bs,  "Cash And Cash Equivalents", "Cash")
    total_assets_s   = _row(bs,  "Total Assets")
    equity_s         = _row(bs,  "Total Stockholder Equity", "Stockholders Equity")

    def _latest(s):
        return float(s[0]) if s else None

    def _growth(s):
        if s and len(s) >= 2 and s[1] and s[1] != 0:
            return (s[0] - s[1]) / abs(s[1])
        return None

    revenue      = _latest(revenue_series)
    revenue_growth = _growth(revenue_series)
    gross_profit = _latest(gross_profit_s)
    net_income   = _latest(net_income_s)
    rd_spend     = _latest(rd_series)
    fcf          = _latest(fcf_series)
    total_debt   = _latest(total_debt_s)
    cash         = _latest(cash_s)
    total_assets = _latest(total_assets_s)
    equity       = _latest(equity_s)

    market_cap   = _safe("marketCap")
    price        = _safe("currentPrice") or _safe("regularMarketPrice")
    pe_ratio     = _safe("trailingPE")
    pb_ratio     = _safe("priceToBook")
    roe          = _safe("returnOnEquity")
    roa          = _safe("returnOnAssets")
    debt_equity  = _safe("debtToEquity")
    current_ratio= _safe("currentRatio")
    op_margin    = _safe("operatingMargins")
    profit_margin= _safe("profitMargins")
    beta         = _safe("beta")
    sector       = _safe("sector", "Unknown")

    gross_margin = (gross_profit / revenue) if revenue and gross_profit else None
    rd_intensity = (rd_spend / revenue) if revenue and rd_spend else None
    fcf_yield    = (fcf / market_cap) if fcf and market_cap else None
    ev_ebit      = None  # skip complex EV calculation
    net_debt     = (total_debt - cash) if total_debt and cash else None
    debt_assets  = (total_debt / total_assets) if total_debt and total_assets else None
    roic         = (net_income / equity) if net_income and equity and equity > 0 else None

    return {
        "ticker": ticker.upper(),
        "price": price,
        "market_cap": market_cap,
        "pe_ratio": pe_ratio,
        "pb_ratio": pb_ratio,
        "roe": roe,
        "roa": roa,
        "roic": roic,
        "debt_equity": debt_equity,
        "current_ratio": current_ratio,
        "op_margin": op_margin,
        "profit_margin": profit_margin,
        "gross_margin": gross_margin,
        "rd_intensity": rd_intensity,
        "revenue": revenue,
        "revenue_growth": revenue_growth,
        "fcf": fcf,
        "fcf_yield": fcf_yield,
        "net_income": net_income,
        "total_debt": total_debt,
        "net_debt": net_debt,
        "debt_assets": debt_assets,
        "cash": cash,
        "beta": beta,
        "sector": sector,
        "ev_ebit": ev_ebit,
    }


def _fmt(v, pct=False, mult=1.0, decimals=2, prefix=""):
    if v is None:
        return "N/A"
    v = v * mult
    if pct:
        return f"{prefix}{v*100:.{decimals}f}%"
    return f"{prefix}{v:.{decimals}f}"


def _facts_block(f: Dict) -> str:
    return (
        f"Ticker: {f['ticker']} | Sector: {f['sector']}\n"
        f"Price: ${_fmt(f['price'])} | Market Cap: ${_fmt(f['market_cap'], mult=1e-9, decimals=1)}B\n"
        f"P/E: {_fmt(f['pe_ratio'])} | P/B: {_fmt(f['pb_ratio'])}\n"
        f"ROE: {_fmt(f['roe'], pct=True)} | ROA: {_fmt(f['roa'], pct=True)} | ROIC: {_fmt(f['roic'], pct=True)}\n"
        f"Operating Margin: {_fmt(f['op_margin'], pct=True)} | Profit Margin: {_fmt(f['profit_margin'], pct=True)}\n"
        f"Gross Margin: {_fmt(f['gross_margin'], pct=True)}\n"
        f"Revenue: ${_fmt(f['revenue'], mult=1e-9, decimals=2)}B | Revenue Growth (YoY): {_fmt(f['revenue_growth'], pct=True)}\n"
        f"FCF: ${_fmt(f['fcf'], mult=1e-9, decimals=2)}B | FCF Yield: {_fmt(f['fcf_yield'], pct=True)}\n"
        f"Total Debt: ${_fmt(f['total_debt'], mult=1e-9, decimals=2)}B | Net Debt: ${_fmt(f['net_debt'], mult=1e-9, decimals=2)}B\n"
        f"Debt/Equity: {_fmt(f['debt_equity'])} | Debt/Assets: {_fmt(f['debt_assets'], pct=True)}\n"
        f"Current Ratio: {_fmt(f['current_ratio'])} | Beta: {_fmt(f['beta'])}\n"
        f"R&D Intensity: {_fmt(f['rd_intensity'], pct=True)}\n"
    )


# ─── Investor agents ──────────────────────────────────────────────────────────

def _agent_warren_buffett(f: Dict) -> Dict:
    system = (
        "You are Warren Buffett. You invest in high-quality businesses with durable moats, "
        "consistent earnings, low debt, strong free cash flow, and a margin of safety. "
        "You avoid speculation and overpaying. Be concise and decisive."
    )
    user = (
        f"Fundamental data:\n{_facts_block(f)}\n\n"
        "Based on these facts, give your investment signal: bullish, bearish, or neutral.\n"
        "Format your response as:\n"
        "Signal: [bullish/bearish/neutral]\n"
        "Confidence: [0-100]\n"
        "Reasoning: [2-3 sentences in Buffett's voice explaining your thesis]\n"
    )
    text = _call_openai(system, user)
    signal, confidence = _parse_llm_signal(text)
    reasoning = ""
    for line in text.splitlines():
        if line.lower().startswith("reasoning"):
            reasoning = line.split(":", 1)[-1].strip()
    return {"agent": "Warren Buffett", "style": "Value / Moat", "signal": signal,
            "confidence": confidence, "reasoning": reasoning or text[:200]}


def _agent_michael_burry(f: Dict) -> Dict:
    system = (
        "You are Michael Burry. You are a contrarian deep-value investor who seeks companies "
        "with high FCF yield, low EV multiples, and hidden asset value that the market ignores. "
        "You are comfortable being early and going against consensus. Be blunt and analytical."
    )
    user = (
        f"Fundamental data:\n{_facts_block(f)}\n\n"
        "Based on these facts, give your investment signal: bullish, bearish, or neutral.\n"
        "Format your response as:\n"
        "Signal: [bullish/bearish/neutral]\n"
        "Confidence: [0-100]\n"
        "Reasoning: [2-3 sentences in Burry's voice explaining your thesis]\n"
    )
    text = _call_openai(system, user)
    signal, confidence = _parse_llm_signal(text)
    reasoning = ""
    for line in text.splitlines():
        if line.lower().startswith("reasoning"):
            reasoning = line.split(":", 1)[-1].strip()
    return {"agent": "Michael Burry", "style": "Deep Value / Contrarian", "signal": signal,
            "confidence": confidence, "reasoning": reasoning or text[:200]}


def _agent_cathie_wood(f: Dict) -> Dict:
    system = (
        "You are Cathie Wood. You invest in disruptive innovation and exponential growth companies. "
        "You value high R&D spend, revenue acceleration, expanding gross margins, and TAM expansion. "
        "You are comfortable with losses today for transformative long-term returns. Be visionary."
    )
    user = (
        f"Fundamental data:\n{_facts_block(f)}\n\n"
        "Based on these facts, give your investment signal: bullish, bearish, or neutral.\n"
        "Format your response as:\n"
        "Signal: [bullish/bearish/neutral]\n"
        "Confidence: [0-100]\n"
        "Reasoning: [2-3 sentences in Wood's voice explaining your thesis]\n"
    )
    text = _call_openai(system, user)
    signal, confidence = _parse_llm_signal(text)
    reasoning = ""
    for line in text.splitlines():
        if line.lower().startswith("reasoning"):
            reasoning = line.split(":", 1)[-1].strip()
    return {"agent": "Cathie Wood", "style": "Disruptive Growth", "signal": signal,
            "confidence": confidence, "reasoning": reasoning or text[:200]}


def _agent_charlie_munger(f: Dict) -> Dict:
    system = (
        "You are Charlie Munger. You seek wonderful businesses at fair prices, not fair businesses at "
        "wonderful prices. You prize ROIC, operating margins, durable competitive advantages, and "
        "rational capital allocation. You are critical of complexity and leverage. Be Socratic."
    )
    user = (
        f"Fundamental data:\n{_facts_block(f)}\n\n"
        "Based on these facts, give your investment signal: bullish, bearish, or neutral.\n"
        "Format your response as:\n"
        "Signal: [bullish/bearish/neutral]\n"
        "Confidence: [0-100]\n"
        "Reasoning: [2-3 sentences in Munger's voice explaining your thesis]\n"
    )
    text = _call_openai(system, user)
    signal, confidence = _parse_llm_signal(text)
    reasoning = ""
    for line in text.splitlines():
        if line.lower().startswith("reasoning"):
            reasoning = line.split(":", 1)[-1].strip()
    return {"agent": "Charlie Munger", "style": "Quality / ROIC", "signal": signal,
            "confidence": confidence, "reasoning": reasoning or text[:200]}


def _agent_nassim_taleb(f: Dict) -> Dict:
    system = (
        "You are Nassim Taleb. You focus on tail risks, fragility, and black-swan exposure. "
        "You are wary of high leverage, complex balance sheets, and companies vulnerable to "
        "extreme events. You prefer asymmetric payoffs and robust businesses. Be philosophical."
    )
    user = (
        f"Fundamental data:\n{_facts_block(f)}\n\n"
        "Based on these facts, give your investment signal: bullish, bearish, or neutral.\n"
        "Format your response as:\n"
        "Signal: [bullish/bearish/neutral]\n"
        "Confidence: [0-100]\n"
        "Reasoning: [2-3 sentences in Taleb's voice explaining the risk/fragility assessment]\n"
    )
    text = _call_openai(system, user)
    signal, confidence = _parse_llm_signal(text)
    reasoning = ""
    for line in text.splitlines():
        if line.lower().startswith("reasoning"):
            reasoning = line.split(":", 1)[-1].strip()
    return {"agent": "Nassim Taleb", "style": "Tail Risk / Antifragility", "signal": signal,
            "confidence": confidence, "reasoning": reasoning or text[:200]}


def _agent_technicals(ticker: str) -> Dict:
    """GeoClaw quant engine as a technical analysis persona."""
    from services.stock_quant_engine import run_quant_analysis
    result = run_quant_analysis(ticker)
    if result.get("error"):
        return {"agent": "Technical Analyst", "style": "RSI / MACD / Bollinger",
                "signal": "neutral", "confidence": 40.0,
                "reasoning": f"Technical data unavailable: {result['error']}"}
    score = result.get("quant_score", 50)
    if score >= 65:
        signal = "bullish"
    elif score <= 35:
        signal = "bearish"
    else:
        signal = "neutral"
    ind = result.get("indicators", {})
    rsi_lbl  = ind.get("rsi", {}).get("label", "")
    macd_lbl = ind.get("macd", {}).get("label", "")
    bb_lbl   = ind.get("bollinger", {}).get("label", "")
    reasoning = (
        f"Quant score {score}/100. "
        f"RSI: {rsi_lbl}. MACD: {macd_lbl}. Bollinger: {bb_lbl}."
    )
    confidence = min(95.0, max(40.0, score if signal == "bullish" else 100 - score))
    return {"agent": "Technical Analyst", "style": "RSI / MACD / Bollinger", "signal": signal,
            "confidence": confidence, "reasoning": reasoning}


# ─── Consensus ────────────────────────────────────────────────────────────────

def _build_consensus(agents: List[Dict]) -> Dict:
    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    total_conf = {"bullish": 0.0, "bearish": 0.0, "neutral": 0.0}
    for a in agents:
        s = a["signal"]
        counts[s] += 1
        total_conf[s] += a["confidence"]
    n = len(agents)
    bullish_pct = round(counts["bullish"] / n * 100)
    bearish_pct = round(counts["bearish"] / n * 100)
    neutral_pct = round(counts["neutral"] / n * 100)

    if counts["bullish"] > counts["bearish"] and counts["bullish"] >= n * 0.4:
        direction = "BUY"
    elif counts["bearish"] > counts["bullish"] and counts["bearish"] >= n * 0.4:
        direction = "SELL"
    else:
        direction = "HOLD"

    avg_bull_conf = round(total_conf["bullish"] / counts["bullish"]) if counts["bullish"] else 0
    avg_bear_conf = round(total_conf["bearish"] / counts["bearish"]) if counts["bearish"] else 0
    dominant_conf = avg_bull_conf if direction == "BUY" else (avg_bear_conf if direction == "SELL" else 50)

    return {
        "direction": direction,
        "bullish_count": counts["bullish"],
        "bearish_count": counts["bearish"],
        "neutral_count": counts["neutral"],
        "bullish_pct": bullish_pct,
        "bearish_pct": bearish_pct,
        "neutral_pct": neutral_pct,
        "confidence": dominant_conf,
        "total_agents": n,
    }


# ─── Public API ───────────────────────────────────────────────────────────────

_AGENT_FNS = [
    lambda f: _agent_warren_buffett(f),
    lambda f: _agent_michael_burry(f),
    lambda f: _agent_cathie_wood(f),
    lambda f: _agent_charlie_munger(f),
    lambda f: _agent_nassim_taleb(f),
]


def run_hedge_fund_analysis(ticker: str) -> Dict[str, Any]:
    """
    Run all 6 investor persona agents on a ticker.
    Returns structured result with per-agent signals and consensus.
    """
    ticker = ticker.upper().strip()
    start = time.time()
    logger.info("hedge_fund: starting analysis for %s", ticker)

    # Fetch fundamentals once (shared across persona agents)
    try:
        fundamentals = _get_fundamentals(ticker)
    except Exception as exc:
        logger.error("hedge_fund: fundamentals fetch failed for %s: %s", ticker, exc)
        fundamentals = {"ticker": ticker, "error": str(exc)}

    results: List[Dict] = []

    # Run persona agents in parallel (5 LLM calls + 1 local technical)
    with ThreadPoolExecutor(max_workers=6) as pool:
        llm_futures = {pool.submit(fn, fundamentals): fn for fn in _AGENT_FNS}
        tech_future = pool.submit(_agent_technicals, ticker)

        for future in as_completed(list(llm_futures.keys()) + [tech_future]):
            try:
                results.append(future.result(timeout=30))
            except Exception as exc:
                logger.warning("hedge_fund: agent failed: %s", exc)
                results.append({
                    "agent": "Unknown", "style": "—",
                    "signal": "neutral", "confidence": 40.0,
                    "reasoning": f"Agent error: {exc}",
                })

    consensus = _build_consensus(results)
    elapsed = round(time.time() - start, 1)

    return {
        "ticker": ticker,
        "consensus": consensus,
        "agents": sorted(results, key=lambda x: x["confidence"], reverse=True),
        "fundamentals_snapshot": {
            k: fundamentals.get(k)
            for k in ("price", "market_cap", "pe_ratio", "roe", "fcf_yield",
                      "revenue_growth", "op_margin", "debt_equity", "beta")
        },
        "analysed_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed,
    }

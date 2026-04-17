import React, { useCallback, useEffect, useRef, useState } from "react";

const API_BASE = "http://127.0.0.1:8000/api";

const css = `
:root {
  --bg: #0a0e17; --card: #111827; --border: #1f2937;
  --text: #f1f5f9; --muted: #64748b; --accent: #3b82f6;
  --bull: #22c55e; --bear: #ef4444; --neutral: #f59e0b;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: ui-sans-serif, system-ui, sans-serif; background: var(--bg); color: var(--text); }
.pulse { animation: pulse 2s infinite; }
@keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:.4 } }
`;

async function api(path) {
  const r = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

function _fmt(value, digits) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "\u2014";
  return n.toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function _fmtClock(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (!Number.isFinite(d.getTime())) return "";
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

// Professional candlestick chart: real axes, grid, crosshair-on-hover tooltip,
// previous-close and last-price reference lines.  Replaces the earlier 92 px
// sparkline with a 260 px chart that actually looks like a trading tool.
function CandleChart({ candles, prevClose, digits = 2 }) {
  const [hover, setHover] = useState(null);
  const bars = Array.isArray(candles)
    ? candles.slice(-60).filter(c => [c.o, c.h, c.l, c.c].every(v => Number.isFinite(Number(v))))
    : [];
  if (bars.length < 2) {
    return (
      <div style={{ height: 260, border: "1px solid var(--border)", borderRadius: 10, background: "#0a0e17",
                    display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)", fontSize: 12 }}>
        Waiting for live candles…
      </div>
    );
  }
  const W = 760, H = 260;
  const padL = 8, padR = 62, padT = 10, padB = 22;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const lows = bars.map(c => Number(c.l));
  const highs = bars.map(c => Number(c.h));
  let minY = Math.min(...lows);
  let maxY = Math.max(...highs);
  if (Number.isFinite(prevClose)) { minY = Math.min(minY, prevClose); maxY = Math.max(maxY, prevClose); }
  const padPct = Math.max(1e-9, (maxY - minY) * 0.04);
  minY -= padPct; maxY += padPct;
  const rangeY = maxY - minY || 1;
  const step = plotW / bars.length;
  const bodyW = Math.max(2, Math.min(9, step * 0.65));
  const y = v => padT + plotH - ((Number(v) - minY) / rangeY) * plotH;
  const x = i => padL + i * step + step / 2;
  const gridLevels = 5;
  const gridVals = Array.from({ length: gridLevels }, (_, i) => minY + (rangeY * i) / (gridLevels - 1));
  const lastBar = bars[bars.length - 1];
  const lastClose = Number(lastBar.c);
  const lastClr = lastClose >= Number(lastBar.o) ? "var(--bull)" : "var(--bear)";

  const handleMove = evt => {
    const rect = evt.currentTarget.getBoundingClientRect();
    const localX = ((evt.clientX - rect.left) / rect.width) * W;
    const idx = Math.max(0, Math.min(bars.length - 1, Math.round((localX - padL - step / 2) / step)));
    const c = bars[idx];
    if (c) setHover({ idx, c, cx: x(idx) });
  };

  return (
    <div style={{ position: "relative", border: "1px solid var(--border)", borderRadius: 10, background: "#0a0e17", overflow: "hidden" }}>
      <svg
        width="100%"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ display: "block", height: 260 }}
        onMouseMove={handleMove}
        onMouseLeave={() => setHover(null)}
      >
        {/* horizontal grid + right-axis price labels */}
        {gridVals.map((v, i) => (
          <g key={`grid-${i}`}>
            <line x1={padL} x2={padL + plotW} y1={y(v)} y2={y(v)} stroke="#1f2937" strokeWidth="1" />
            <text x={W - padR + 6} y={y(v) + 4} fontSize="10" fill="#64748b" fontFamily="ui-monospace, monospace">
              {_fmt(v, digits)}
            </text>
          </g>
        ))}
        {/* vertical grid — quartile marks */}
        {[0.25, 0.5, 0.75].map((frac, i) => (
          <line key={`v-${i}`}
            x1={padL + plotW * frac} x2={padL + plotW * frac}
            y1={padT} y2={padT + plotH}
            stroke="#1f2937" strokeWidth="1" strokeDasharray="2 3" opacity="0.55" />
        ))}
        {/* prev-close reference */}
        {Number.isFinite(prevClose) && (
          <g>
            <line x1={padL} x2={padL + plotW} y1={y(prevClose)} y2={y(prevClose)}
              stroke="#64748b" strokeWidth="1" strokeDasharray="5 4" opacity="0.6" />
            <rect x={W - padR} y={y(prevClose) - 8} width={padR - 4} height={16} rx="3" fill="#1f2937" />
            <text x={W - padR + 4} y={y(prevClose) + 4} fontSize="10" fontWeight="700" fill="#94a3b8" fontFamily="ui-monospace, monospace">
              PC {_fmt(prevClose, digits)}
            </text>
          </g>
        )}
        {/* last-price reference */}
        <g>
          <line x1={padL} x2={padL + plotW} y1={y(lastClose)} y2={y(lastClose)}
            stroke={lastClr} strokeWidth="1" strokeDasharray="1 3" opacity="0.85" />
          <rect x={W - padR} y={y(lastClose) - 9} width={padR - 4} height={18} rx="3" fill={lastClr} opacity="0.95" />
          <text x={W - padR + 4} y={y(lastClose) + 4} fontSize="11" fontWeight="800" fill="#0a0e17" fontFamily="ui-monospace, monospace">
            {_fmt(lastClose, digits)}
          </text>
        </g>
        {/* candles */}
        {bars.map((c, i) => {
          const open = Number(c.o), high = Number(c.h), low = Number(c.l), close = Number(c.c);
          const up = close >= open;
          const color = up ? "var(--bull)" : "var(--bear)";
          const cx = x(i);
          const top = Math.min(y(open), y(close));
          const height = Math.max(1.5, Math.abs(y(open) - y(close)));
          return (
            <g key={`${c.quote_minute || c.t || i}-${i}`}>
              <line x1={cx} x2={cx} y1={y(high)} y2={y(low)} stroke={color} strokeWidth="1" opacity="0.95" />
              <rect x={cx - bodyW / 2} y={top} width={bodyW} height={height} rx="1" fill={color} opacity={up ? 0.85 : 0.9} />
            </g>
          );
        })}
        {/* bottom axis labels — first, mid, last timestamp */}
        {[0, Math.floor(bars.length / 2), bars.length - 1].map((idx, i) => (
          <text key={`t-${i}`}
            x={x(idx)} y={H - 6}
            fontSize="10" fill="#64748b"
            textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"}
            fontFamily="ui-monospace, monospace">
            {_fmtClock(bars[idx].quote_minute || bars[idx].quote_timestamp || bars[idx].t)}
          </text>
        ))}
        {/* crosshair */}
        {hover && (
          <g pointerEvents="none">
            <line x1={hover.cx} x2={hover.cx} y1={padT} y2={padT + plotH}
              stroke="#f1f5f9" strokeWidth="1" strokeDasharray="3 3" opacity="0.35" />
          </g>
        )}
      </svg>
      {hover && (
        <div style={{
          position: "absolute", top: 8, left: 12,
          background: "rgba(17,24,39,0.92)", border: "1px solid var(--border)",
          borderRadius: 6, padding: "6px 10px", fontSize: 11, color: "var(--text)",
          fontFamily: "ui-monospace, monospace", pointerEvents: "none", lineHeight: 1.5,
        }}>
          <div style={{ color: "var(--muted)" }}>
            {_fmtClock(hover.c.quote_minute || hover.c.quote_timestamp || hover.c.t)}
          </div>
          <div>O <b>{_fmt(hover.c.o, digits)}</b>   H <b style={{ color: "var(--bull)" }}>{_fmt(hover.c.h, digits)}</b></div>
          <div>L <b style={{ color: "var(--bear)" }}>{_fmt(hover.c.l, digits)}</b>   C <b>{_fmt(hover.c.c, digits)}</b></div>
        </div>
      )}
    </div>
  );
}

function Card({ children, style }) {
  return <div style={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 12, padding: 16, ...style }}>{children}</div>;
}

function Label({ children }) {
  return <div style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>{children}</div>;
}

const DEFAULT_INSTRUMENTS = [
  { symbol: "JP225", label: "JP225", name: "Nikkei 225 proxy", asset_class: "index" },
  { symbol: "USA500", label: "USA500", name: "S&P 500 proxy", asset_class: "index" },
  { symbol: "TSLA", label: "TSLA", name: "Tesla, Inc.", asset_class: "equity" },
  { symbol: "NVDA", label: "NVDA", name: "NVIDIA Corporation", asset_class: "equity" },
  { symbol: "META", label: "META", name: "Meta Platforms, Inc.", asset_class: "equity" },
  { symbol: "AMZN", label: "AMZN", name: "Amazon.com, Inc.", asset_class: "equity" },
  { symbol: "INTC", label: "INTC", name: "Intel Corporation", asset_class: "equity" },
  { symbol: "MU", label: "MU", name: "Micron Technology, Inc.", asset_class: "equity" },
  { symbol: "GOLD", label: "GOLD", name: "Gold spot (XAU/USD)", asset_class: "metal" },
  { symbol: "SILVER", label: "SILVER", name: "Silver spot (XAG/USD)", asset_class: "metal" },
];

export default function App() {
  const [instruments, setInstruments] = useState(DEFAULT_INSTRUMENTS);
  const [activeSymbol, setActiveSymbol] = useState("JP225");
  const [live, setLive] = useState(null);
  const [chartInterval, setChartInterval] = useState("1");
  const [bias, setBias] = useState("\u2014");
  const [bullCount, setBullCount] = useState(0);
  const [bearCount, setBearCount] = useState(0);
  const [signals, setSignals] = useState([]);
  const [neuralSchema, setNeuralSchema] = useState(null);
  const [news, setNews] = useState([]);
  const [briefing, setBriefing] = useState("");
  const [err, setErr] = useState("");
  const [now, setNow] = useState(new Date());
  const prevPrice = useRef(null);
  const [flash, setFlash] = useState(null);
  const [lastTick, setLastTick] = useState(null);
  const [tickAge, setTickAge] = useState(0);

  useEffect(() => {
    const t = setInterval(() => {
      setNow(new Date());
      if (lastTick) setTickAge(Math.floor((Date.now() - lastTick) / 1000));
    }, 1000);
    return () => clearInterval(t);
  }, [lastTick]);

  // Instruments — populate the asset switcher.
  useEffect(() => {
    (async () => {
      try {
        const d = await api("/instruments");
        if (Array.isArray(d.instruments) && d.instruments.length) {
          setInstruments(d.instruments);
        }
      } catch { /* keep DEFAULT_INSTRUMENTS */ }
    })();
  }, []);

  // Reset transient tick/flash state whenever the user switches asset so the
  // hero card does not briefly render the previous symbol's price.
  useEffect(() => {
    setLive(null);
    prevPrice.current = null;
    setFlash(null);
    setLastTick(null);
    setTickAge(0);
  }, [activeSymbol]);

  // Live quote — refresh every 2s for intraminute chart accuracy.
  const loadLive = useCallback(async () => {
    try {
      const d = await api(`/live/${encodeURIComponent(activeSymbol)}?interval=${encodeURIComponent(chartInterval)}`);
      if (d.error) return;
      if (prevPrice.current !== null && d.price !== prevPrice.current) {
        setFlash(d.price > prevPrice.current ? "up" : "down");
        setTimeout(() => setFlash(null), 600);
      }
      prevPrice.current = d.price;
      setLive(d);
      const quoteMs = d.quote_timestamp ? Date.parse(d.quote_timestamp) : Date.now();
      const safeQuoteMs = Number.isFinite(quoteMs) ? quoteMs : Date.now();
      setLastTick(safeQuoteMs);
      setTickAge(Math.max(0, Math.floor((Date.now() - safeQuoteMs) / 1000)));
    } catch { /* silent */ }
  }, [activeSymbol, chartInterval]);

  // Signals + overview — refresh every 60s
  const loadOverview = useCallback(async () => {
    try {
      const d = await api("/dashboard/overview");
      const sigs = d.signals || [];
      setBias(d.market_bias?.label || "NEUTRAL");
      setBullCount(sigs.filter(s => ["BUY","BULLISH"].includes((s.direction||"").toUpperCase())).length);
      setBearCount(sigs.filter(s => ["SELL","BEARISH"].includes((s.direction||"").toUpperCase())).length);
      setSignals(sigs.slice(0, 25));
    } catch (e) { setErr(String(e)); }
  }, []);

  // Neural schema — refresh every 60s
  const loadNeural = useCallback(async () => {
    try { const d = await api("/intelligence/jp225"); setNeuralSchema(d.error ? null : d); } catch { }
  }, []);

  // News — per-asset, refresh every 30 s (backend caches for 45 s so this
  // polls the cache mostly, but keeps the UI fresh when new headlines land).
  const loadNews = useCallback(async () => {
    try {
      const d = await api(`/news?symbol=${encodeURIComponent(activeSymbol)}`);
      setNews((d.news || []).slice(0, 12));
    } catch { }
  }, [activeSymbol]);

  // Briefing — refresh every 5 min
  const loadBriefing = useCallback(async () => {
    try { const d = await api("/briefing"); setBriefing(d.briefing || ""); } catch { }
  }, []);

  useEffect(() => { loadLive(); const t = setInterval(loadLive, 2000); return () => clearInterval(t); }, [loadLive]);
  useEffect(() => { loadOverview(); const t = setInterval(loadOverview, 60000); return () => clearInterval(t); }, [loadOverview]);
  useEffect(() => { loadNeural(); const t = setInterval(loadNeural, 60000); return () => clearInterval(t); }, [loadNeural]);
  useEffect(() => { setNews([]); loadNews(); const t = setInterval(loadNews, 30000); return () => clearInterval(t); }, [loadNews]);
  useEffect(() => { loadBriefing(); const t = setInterval(loadBriefing, 300000); return () => clearInterval(t); }, [loadBriefing]);

  const biasColor = bias === "BULLISH" ? "var(--bull)" : bias === "BEARISH" ? "var(--bear)" : "var(--neutral)";
  const priceColor = live?.direction === "up" ? "var(--bull)" : live?.direction === "down" ? "var(--bear)" : "var(--text)";
  const flashBg = flash === "up" ? "#22c55e22" : flash === "down" ? "#ef444422" : "transparent";
  const activeInstrument = instruments.find(i => i.symbol === activeSymbol) || instruments[0] || DEFAULT_INSTRUMENTS[0];
  const liveName = live?.name || activeInstrument?.name || activeSymbol;
  const liveLabel = live?.symbol || activeInstrument?.label || activeSymbol;
  const isMetal = (live?.asset_class || activeInstrument?.asset_class) === "metal";
  const isEquity = (live?.asset_class || activeInstrument?.asset_class) === "equity";
  const priceDigits = isMetal ? 2 : (isEquity ? 2 : 0);
  const changeDigits = isMetal ? 2 : (isEquity ? 2 : 0);
  const liveSource = live?.source_symbol ? `${live.source || "Source"} ${live.source_symbol}` : `TradingView ${activeInstrument?.source_symbol || activeSymbol}`;
  const chartIntervalLabel = live?.chart_basis?.interval || "1m";
  const changeBasis = live?.change_basis === "same_source_1m_candle_vs_previous_close" ? `${chartIntervalLabel} candle vs prev close` : (live?.change_basis || "live basis");
  const quoteAge = Math.max(Number(live?.quote_age_seconds ?? 0), tickAge);
  const isDelayed = Boolean(live?.is_stale || live?.freshness === "delayed");
  const quoteAgeLabel = quoteAge < 60 ? `${quoteAge}s ago` : `${Math.floor(quoteAge / 60)}m ago`;
  const sourceStatus = live?.source_status ? ` · Status: ${String(live.source_status).replaceAll("_", " ")}` : "";
  const marketContext = live?.market_context || {};
  const drivers = marketContext.drivers || [];
  const sensitivity = marketContext.sensitivity || [];

  return (
    <>
      <style>{css}</style>
      <div style={{ maxWidth: 1280, margin: "0 auto", padding: "16px 20px" }}>

        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div>
              <div style={{ fontSize: 11, color: "var(--muted)", letterSpacing: "0.1em", textTransform: "uppercase" }}>GeoClaw Intelligence</div>
              <h1 style={{ margin: "2px 0 0", fontSize: 20, fontWeight: 700 }}>{liveName} · {isDelayed ? "Delayed" : "Live"}</h1>
            </div>
            <select
              value={activeSymbol}
              onChange={e => setActiveSymbol(e.target.value)}
              aria-label="Select asset"
              style={{
                background: "var(--card)",
                color: "var(--text)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: "6px 10px",
                fontSize: 13,
                fontWeight: 700,
                cursor: "pointer",
              }}
            >
              {instruments.map(inst => (
                <option key={inst.symbol} value={inst.symbol}>{inst.label} · {inst.name}</option>
              ))}
            </select>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ fontSize: 13, color: "var(--muted)" }}>
              {now.toLocaleString("en-GB", { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" })}
            </div>
            <div style={{ fontSize: 12, color: isDelayed ? "var(--bear)" : "var(--bull)", fontVariantNumeric: "tabular-nums" }}>
              {lastTick ? (isDelayed ? `delayed · ${quoteAgeLabel}` : `updated ${quoteAgeLabel}`) : "connecting…"}
            </div>
            <div style={{ width: 8, height: 8, borderRadius: "50%", background: isDelayed ? "var(--bear)" : "var(--bull)" }} className="pulse" />
          </div>
        </div>

        {err && <div style={{ color: "var(--bear)", fontSize: 12, marginBottom: 12 }}>{err}</div>}

        {/* Live price hero */}
        <Card style={{ marginBottom: 16, transition: "background 0.4s", background: flash ? flashBg : "var(--card)" }}>
          <div style={{ display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 24, alignItems: "center" }}>
            <div>
              <Label>{liveName} · {liveSource}</Label>
              <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
                <span style={{ fontSize: 48, fontWeight: 800, color: priceColor, fontVariantNumeric: "tabular-nums" }}>
                  {live ? Number(live.price).toLocaleString("en-GB", { maximumFractionDigits: priceDigits }) : "\u2014"}
                </span>
                {live && (
                  <span style={{ fontSize: 20, fontWeight: 600, color: priceColor }}>
                    {live.change >= 0 ? "\u25b2" : "\u25bc"} {Math.abs(live.change).toLocaleString("en-GB", { maximumFractionDigits: changeDigits })} ({live.change_pct >= 0 ? "+" : ""}{live.change_pct.toFixed(2)}%)
                  </span>
                )}
              </div>
              {live && (
                <div style={{ display: "flex", gap: 20, marginTop: 8, fontSize: 13, color: "var(--muted)" }}>
                  <span>Open <b style={{ color: "var(--text)" }}>{Number(live.open).toLocaleString()}</b></span>
                  <span>H <b style={{ color: "var(--bull)" }}>{Number(live.day_high).toLocaleString()}</b></span>
                  <span>L <b style={{ color: "var(--bear)" }}>{Number(live.day_low).toLocaleString()}</b></span>
                  <span>Prev Close <b style={{ color: "var(--text)" }}>{Number(live.prev_close).toLocaleString()}</b></span>
                  {live.bid != null && <span>Bid <b style={{ color: "var(--text)" }}>{Number(live.bid).toLocaleString()}</b></span>}
                  {live.ask != null && <span>Ask <b style={{ color: "var(--text)" }}>{Number(live.ask).toLocaleString()}</b></span>}
                </div>
              )}
              {live && (
                <div style={{ marginTop: 6, fontSize: 11, color: "var(--muted)", maxWidth: 520 }}>
                  Source: {liveSource} · Updated: {quoteAgeLabel} · Session: {live.session || "Tokyo"} · Type: {live.market_type || "CFD"}{sourceStatus} · {changeBasis}.
                  {isDelayed ? " Delayed source." : ` Same-source ${chartIntervalLabel} candle basis.`}
                  {live.fallback_reason ? ` Fallback: ${live.fallback_reason}.` : ""}
                </div>
              )}
            </div>
            <div style={{ minWidth: 0 }}>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 6, marginBottom: 6 }}>
                {[
                  ["1", "1m"],
                  ["30", "30m"],
                  ["60", "1h"],
                ].map(([value, label]) => (
                  <button
                    key={value}
                    onClick={() => setChartInterval(value)}
                    style={{
                      border: `1px solid ${chartInterval === value ? "var(--accent)" : "var(--border)"}`,
                      background: chartInterval === value ? "#1d4ed822" : "#0a0e17",
                      color: chartInterval === value ? "var(--text)" : "var(--muted)",
                      borderRadius: 999,
                      padding: "4px 9px",
                      fontSize: 11,
                      fontWeight: 800,
                      cursor: "pointer"
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <CandleChart
                candles={live?.candles}
                prevClose={Number(live?.prev_close)}
                digits={priceDigits}
              />
              {live?.chart_basis && (
                <div style={{ marginTop: 6, fontSize: 11, color: "var(--muted)", textAlign: "right" }}>
                  Chart: {live.chart_basis.bars} × {live.chart_basis.interval} {live.chart_basis.source_symbol} candles
                </div>
              )}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 120 }}>
              <div style={{ textAlign: "center", padding: "10px 16px", borderRadius: 8, background: biasColor + "22", border: `1px solid ${biasColor}44` }}>
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 2 }}>BIAS</div>
                <div style={{ fontSize: 18, fontWeight: 800, color: biasColor }}>{bias}</div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                <div style={{ textAlign: "center", padding: "6px 8px", borderRadius: 6, background: "#22c55e11", border: "1px solid #22c55e33" }}>
                  <div style={{ fontSize: 10, color: "var(--muted)" }}>BULL</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: "var(--bull)" }}>{bullCount}</div>
                </div>
                <div style={{ textAlign: "center", padding: "6px 8px", borderRadius: 6, background: "#ef444411", border: "1px solid #ef444433" }}>
                  <div style={{ fontSize: 10, color: "var(--muted)" }}>BEAR</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: "var(--bear)" }}>{bearCount}</div>
                </div>
              </div>
            </div>
          </div>
        </Card>

        {live?.market_context && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
            <Card>
              <Label>{marketContext.title || `Why ${liveLabel} is moving today`}</Label>
              <div style={{ fontSize: 14, lineHeight: 1.55, marginBottom: 12 }}>
                {marketContext.summary}
              </div>
              <div style={{ display: "grid", gap: 8, marginBottom: 12 }}>
                {drivers.map((d, idx) => {
                  const impactColor = d.impact === "bullish" ? "var(--bull)" : d.impact === "bearish" ? "var(--bear)" : "var(--neutral)";
                  return (
                    <div key={idx} style={{ padding: "8px 10px", background: "#0a0e17", border: "1px solid var(--border)", borderRadius: 8 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, marginBottom: 4 }}>
                        <b style={{ fontSize: 13 }}>{d.label}</b>
                        <span style={{ color: impactColor, fontSize: 11, fontWeight: 800, textTransform: "uppercase" }}>{d.impact}</span>
                      </div>
                      <div style={{ color: "var(--muted)", fontSize: 12, lineHeight: 1.45 }}>{d.why}</div>
                    </div>
                  );
                })}
              </div>
              <div style={{ display: "grid", gap: 6 }}>
                {(marketContext.articles || []).slice(0, 3).map((a, idx) => (
                  <a key={idx} href={a.url} target="_blank" rel="noreferrer"
                    style={{ color: "var(--accent)", fontSize: 12, textDecoration: "none", lineHeight: 1.35 }}>
                    {a.title} <span style={{ color: "var(--muted)" }}>· {a.source}</span>
                  </a>
                ))}
              </div>
              <div style={{ marginTop: 10, color: "var(--muted)", fontSize: 11 }}>{marketContext.disclaimer}</div>
            </Card>

            <Card>
              <Label>Sensitivity map</Label>
              <div style={{ display: "grid", gap: 10 }}>
                {sensitivity.map((s, idx) => {
                  const val = Math.max(-100, Math.min(100, Number(s.score || 0)));
                  const positive = val >= 0;
                  const barWidth = Math.abs(val) / 2;
                  return (
                    <div key={idx}>
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 12, marginBottom: 4 }}>
                        <b>{s.factor}</b>
                        <span style={{ color: positive ? "var(--bull)" : "var(--bear)", fontWeight: 800 }}>
                          {positive ? "+" : ""}{val}
                        </span>
                      </div>
                      <div style={{ height: 7, background: "#0a0e17", border: "1px solid var(--border)", borderRadius: 999, overflow: "hidden" }}>
                        <div style={{
                          width: `${barWidth}%`,
                          marginLeft: positive ? "50%" : `${50 - barWidth}%`,
                          height: "100%",
                          background: positive ? "var(--bull)" : "var(--bear)",
                          opacity: 0.85
                        }} />
                      </div>
                      <div style={{ marginTop: 4, color: "var(--muted)", fontSize: 11 }}>
                        {s.current_signal} · {s.why}
                      </div>
                    </div>
                  );
                })}
              </div>
            </Card>
          </div>
        )}

        {neuralSchema && (
          <Card style={{ marginBottom: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <Label style={{ margin: 0 }}>Neural Intelligence Schema · JP225</Label>
              <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                <span style={{ fontSize: 12, color: "var(--muted)" }}>
                  {neuralSchema.llm_provider} · {neuralSchema.elapsed_seconds}s · {neuralSchema.cached ? "cached" : "live"}
                </span>
                <div style={{
                  padding: "4px 14px", borderRadius: 999, fontWeight: 800, fontSize: 13,
                  background: neuralSchema.bias === "BULLISH" ? "#22c55e22" : neuralSchema.bias === "BEARISH" ? "#ef444422" : "#f59e0b22",
                  color: neuralSchema.bias === "BULLISH" ? "var(--bull)" : neuralSchema.bias === "BEARISH" ? "var(--bear)" : "var(--neutral)",
                  border: `1px solid ${neuralSchema.bias === "BULLISH" ? "#22c55e44" : neuralSchema.bias === "BEARISH" ? "#ef444444" : "#f59e0b44"}`,
                }}>{neuralSchema.bias} {neuralSchema.confidence}%</div>
              </div>
            </div>

            {/* Thesis + trade note */}
            <div style={{ fontSize: 14, lineHeight: 1.6, marginBottom: 12, color: "var(--text)" }}>
              <b>Thesis:</b> {neuralSchema.short_thesis}
            </div>
            {neuralSchema.trade_note && (
              <div style={{ fontSize: 13, lineHeight: 1.55, color: "var(--muted)", marginBottom: 14, padding: "10px 12px", background: "#0a0e17", borderRadius: 8, borderLeft: "3px solid var(--accent)" }}>
                {neuralSchema.trade_note}
              </div>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              {/* Factor bars */}
              <div>
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.07em" }}>
                  Factor Scores · Composite {neuralSchema.composite_score > 0 ? "+" : ""}{neuralSchema.composite_score}
                </div>
                <div style={{ display: "grid", gap: 6 }}>
                  {(neuralSchema.factors || []).map(f => {
                    const pct = Math.min(100, Math.abs(f.score));
                    const bull = f.direction === "bullish";
                    const bear = f.direction === "bearish";
                    const c = bull ? "var(--bull)" : bear ? "var(--bear)" : "var(--muted)";
                    return (
                      <div key={f.id}>
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 3 }}>
                          <span style={{ color: "var(--text)" }}>{f.label}</span>
                          <span style={{ color: c, fontWeight: 700 }}>{f.score > 0 ? "+" : ""}{f.score}</span>
                        </div>
                        <div style={{ height: 5, background: "#1f2937", borderRadius: 999, overflow: "hidden" }}>
                          <div style={{ width: `${pct}%`, height: "100%", background: c, opacity: 0.85 }} />
                        </div>
                        <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>
                          {f.change_pct != null ? `${f.change_pct > 0 ? "+" : ""}${f.change_pct.toFixed(2)}%` : "—"} · {f.description}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Key driver + risk + news signal */}
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div style={{ padding: "10px 12px", background: "#0a0e17", border: "1px solid #22c55e33", borderRadius: 8 }}>
                  <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>KEY DRIVER</div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "var(--bull)" }}>{neuralSchema.key_driver}</div>
                </div>
                <div style={{ padding: "10px 12px", background: "#0a0e17", border: "1px solid #ef444433", borderRadius: 8 }}>
                  <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>RISK FACTOR</div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "var(--bear)" }}>{neuralSchema.risk_factor}</div>
                </div>
                {neuralSchema.news_signal && (
                  <div style={{ padding: "10px 12px", background: "#0a0e17", border: "1px solid var(--border)", borderRadius: 8 }}>
                    <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 6 }}>NEWS NLP · {neuralSchema.news_signal.headlines_scanned} headlines</div>
                    <div style={{ display: "flex", gap: 16, fontSize: 13 }}>
                      <span style={{ color: "var(--bull)" }}>▲ {neuralSchema.news_signal.bull_score} bullish</span>
                      <span style={{ color: "var(--bear)" }}>▼ {neuralSchema.news_signal.bear_score} bearish</span>
                    </div>
                    {(neuralSchema.news_signal.bullish_hits || []).slice(0, 2).map((h, i) => (
                      <div key={i} style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>✓ {h}</div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </Card>
        )}

        {/* Briefing + News */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
          <Card>
            <Label>AI Briefing</Label>
            <div style={{ fontSize: 13, lineHeight: 1.7, maxHeight: 280, overflowY: "auto", whiteSpace: "pre-wrap", color: "var(--text)" }}>
              {briefing || <span style={{ color: "var(--muted)" }}>Loading…</span>}
            </div>
          </Card>
          <Card>
            <Label>Latest News</Label>
            <div style={{ display: "flex", flexDirection: "column", gap: 10, maxHeight: 280, overflowY: "auto" }}>
              {news.length === 0 && <div style={{ color: "var(--muted)", fontSize: 13 }}>No news.</div>}
              {news.map(n => (
                <div key={n.id} style={{ borderBottom: "1px solid var(--border)", paddingBottom: 8 }}>
                  <a href={n.url} target="_blank" rel="noreferrer"
                    style={{ color: "var(--accent)", fontSize: 13, fontWeight: 500, textDecoration: "none", lineHeight: 1.4, display: "block" }}>
                    {n.headline}
                  </a>
                  <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 3 }}>
                    {n.source} · {n.ts ? new Date(n.ts).toLocaleTimeString() : ""}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>

        {/* Signals */}
        <Card>
          <Label>Signals ({signals.length})</Label>
          {signals.length === 0
            ? <div style={{ color: "var(--muted)", fontSize: 13 }}>No signals yet.</div>
            : <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {signals.map(s => {
                const dir = (s.direction || "").toUpperCase();
                const isBull = ["BUY","BULLISH"].includes(dir);
                const isBear = ["SELL","BEARISH"].includes(dir);
                const c = isBull ? "var(--bull)" : isBear ? "var(--bear)" : "var(--muted)";
                return (
                  <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", background: "#0a0e17", borderRadius: 6, border: "1px solid var(--border)" }}>
                    <div style={{ width: 28, fontSize: 11, fontWeight: 700, color: c, flexShrink: 0 }}>{isBull ? "BUY" : isBear ? "SELL" : "—"}</div>
                    <div style={{ flex: 1, fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.signal_name}</div>
                    <div style={{ width: 60, height: 4, background: "#1f2937", borderRadius: 2, overflow: "hidden", flexShrink: 0 }}>
                      <div style={{ width: `${Math.min(100, s.confidence || 0)}%`, height: "100%", background: c }} />
                    </div>
                    <div style={{ fontSize: 12, color: "var(--muted)", minWidth: 34, textAlign: "right" }}>{Math.round(s.confidence || 0)}%</div>
                  </div>
                );
              })}
            </div>
          }
        </Card>

        <div style={{ marginTop: 12, fontSize: 11, color: "var(--muted)", textAlign: "center" }}>
          Live price + current candle every 2s · Signals every 60s · GeoClaw
        </div>
      </div>
    </>
  );
}

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

const API_BASE = "http://127.0.0.1:8000/api";

// -----------------------------------------------------------------------------
// Bloomberg-terminal palette.  Amber/orange on pure black with bull/bear accents
// and a monospace stack.  Sharp rectangles, no rounded cards, tight padding.
// -----------------------------------------------------------------------------
const css = `
:root {
  --bg: #000000;
  --panel: #050505;
  --panel-hi: #0a0a0a;
  --border: #1a1a1a;
  --border-hi: #2a2a2a;
  --amber: #ff9e18;
  --amber-dim: #b36a00;
  --text: #e8e8e8;
  --text-hi: #ffffff;
  --muted: #7a7a7a;
  --muted-hi: #a0a0a0;
  --bull: #22e670;
  --bear: #ff3d3d;
  --yellow: #ffe500;
  --mono: "IBM Plex Mono", "JetBrains Mono", "Source Code Pro", "Menlo", "Consolas", ui-monospace, monospace;
}
* { box-sizing: border-box; }
html, body, #root { background: var(--bg); color: var(--text); }
body {
  margin: 0;
  font-family: var(--mono);
  font-size: 13px;
  line-height: 1.35;
  font-variant-numeric: tabular-nums;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--amber); text-decoration: none; }
a:hover { color: var(--yellow); text-decoration: underline; }

.panel { border: 1px solid var(--border); background: var(--panel); }
.panel-head {
  background: var(--panel-hi);
  border-bottom: 1px solid var(--border);
  padding: 4px 10px;
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--amber);
  font-weight: 700;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.panel-body { padding: 10px 12px; }

.amber { color: var(--amber); }
.bull { color: var(--bull); }
.bear { color: var(--bear); }
.dim { color: var(--muted); }
.hi { color: var(--text-hi); }

.label {
  font-size: 10px;
  letter-spacing: 0.12em;
  color: var(--amber);
  text-transform: uppercase;
  font-weight: 700;
}

.kv { display: flex; justify-content: space-between; gap: 8px; }
.kv .k { color: var(--muted); text-transform: uppercase; font-size: 10px; letter-spacing: 0.06em; }
.kv .v { color: var(--text-hi); }

button.fn {
  background: #1a1000;
  color: var(--amber);
  border: 1px solid var(--amber-dim);
  padding: 2px 8px;
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.06em;
  cursor: pointer;
}
button.fn:hover, button.fn.on { background: var(--amber); color: #000; }

select.cli {
  background: #000;
  color: var(--amber);
  border: 1px solid var(--amber-dim);
  padding: 3px 6px 3px 8px;
  font-family: var(--mono);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.06em;
  cursor: pointer;
}
select.cli:focus { outline: 1px solid var(--amber); }

input.cli {
  background: #000;
  color: var(--amber);
  border: 1px solid var(--amber-dim);
  padding: 3px 8px;
  font-family: var(--mono);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  width: 90px;
}
input.cli:focus { outline: 1px solid var(--amber); }

.ticker {
  display: flex;
  overflow: hidden;
  white-space: nowrap;
  background: #000;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
}
.ticker-track {
  display: inline-flex;
  animation: ticker 85s linear infinite;
  padding-left: 100%;
}
.ticker-track.paused { animation-play-state: paused; }
.ticker-item {
  display: inline-flex;
  gap: 6px;
  padding: 4px 18px;
  border-right: 1px solid var(--border);
  font-size: 12px;
}
@keyframes ticker {
  0%   { transform: translateX(0); }
  100% { transform: translateX(-100%); }
}

.flash-up { animation: flashUp 0.7s ease-out; }
.flash-dn { animation: flashDn 0.7s ease-out; }
@keyframes flashUp { 0% { background:#22e67033 } 100% { background:transparent } }
@keyframes flashDn { 0% { background:#ff3d3d33 } 100% { background:transparent } }

.dot { width:7px; height:7px; border-radius:50%; display:inline-block; }
.pulse { animation: pulse 1.4s infinite; }
@keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:.35 } }

table.tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
table.tbl th {
  color: var(--amber);
  text-align: left;
  font-weight: 700;
  text-transform: uppercase;
  font-size: 10px;
  letter-spacing: 0.1em;
  padding: 4px 8px;
  border-bottom: 1px solid var(--border);
  background: #050505;
}
table.tbl td {
  padding: 4px 8px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
table.tbl tr:hover td { background: #0c0c0c; }

.num { font-variant-numeric: tabular-nums; }
.strong-num { font-weight: 700; font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }

::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border-hi); }
`;

async function api(path) {
  const r = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

// -----------------------------------------------------------------------------
// Formatting helpers
// -----------------------------------------------------------------------------
function fmt(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "\u2014";
  return n.toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtSigned(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "\u2014";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function fmtClock(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (!Number.isFinite(d.getTime())) return "";
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function fmtHMS(date) {
  return date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function digitsFor(assetClass) {
  if (assetClass === "metal" || assetClass === "equity") return 2;
  return 0;
}

// -----------------------------------------------------------------------------
// Candlestick chart (amber/black palette).
// -----------------------------------------------------------------------------
function CandleChart({ candles, prevClose, digits = 2, height = 300 }) {
  const [hover, setHover] = useState(null);
  const bars = Array.isArray(candles)
    ? candles.slice(-60).filter(c => [c.o, c.h, c.l, c.c].every(v => Number.isFinite(Number(v))))
    : [];
  if (bars.length < 2) {
    return (
      <div style={{ height, border: "1px solid var(--border)", background: "#000",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    color: "var(--muted)", fontSize: 11, letterSpacing: "0.1em" }}>
        {"ACQUIRING LIVE CANDLES\u2026"}
      </div>
    );
  }
  const W = 960;
  const H = height;
  const padL = 6, padR = 74, padT = 10, padB = 22;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const lows = bars.map(c => Number(c.l));
  const highs = bars.map(c => Number(c.h));
  let minY = Math.min(...lows);
  let maxY = Math.max(...highs);
  if (Number.isFinite(prevClose) && prevClose > 0) {
    minY = Math.min(minY, prevClose);
    maxY = Math.max(maxY, prevClose);
  }
  const padPct = Math.max(1e-9, (maxY - minY) * 0.04);
  minY -= padPct; maxY += padPct;
  const rangeY = maxY - minY || 1;
  const step = plotW / bars.length;
  const bodyW = Math.max(2, Math.min(10, step * 0.7));
  const y = v => padT + plotH - ((Number(v) - minY) / rangeY) * plotH;
  const x = i => padL + i * step + step / 2;

  const gridLevels = 6;
  const gridVals = Array.from({ length: gridLevels }, (_, i) => minY + (rangeY * i) / (gridLevels - 1));

  const lastBar = bars[bars.length - 1];
  const lastClose = Number(lastBar.c);
  const lastUp = lastClose >= Number(lastBar.o);
  const lastClr = lastUp ? "var(--bull)" : "var(--bear)";

  const handleMove = evt => {
    const rect = evt.currentTarget.getBoundingClientRect();
    const localX = ((evt.clientX - rect.left) / rect.width) * W;
    const idx = Math.max(0, Math.min(bars.length - 1, Math.round((localX - padL - step / 2) / step)));
    const c = bars[idx];
    if (c) setHover({ idx, c, cx: x(idx) });
  };

  return (
    <div style={{ position: "relative", border: "1px solid var(--border)", background: "#000" }}>
      <svg
        width="100%"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ display: "block", height }}
        onMouseMove={handleMove}
        onMouseLeave={() => setHover(null)}
      >
        {gridVals.map((v, i) => (
          <g key={`grid-${i}`}>
            <line x1={padL} x2={padL + plotW} y1={y(v)} y2={y(v)}
                  stroke="#151515" strokeWidth="1" />
            <text x={W - padR + 6} y={y(v) + 4}
                  fontSize="10" fill="#9a6a00"
                  fontFamily="var(--mono)" fontWeight="700" letterSpacing="0.05em">
              {fmt(v, digits)}
            </text>
          </g>
        ))}
        {[0.25, 0.5, 0.75].map((frac, i) => (
          <line key={`v-${i}`}
                x1={padL + plotW * frac} x2={padL + plotW * frac}
                y1={padT} y2={padT + plotH}
                stroke="#1a1a1a" strokeWidth="1" strokeDasharray="2 4" opacity="0.8" />
        ))}
        {Number.isFinite(prevClose) && prevClose > 0 && (
          <g>
            <line x1={padL} x2={padL + plotW} y1={y(prevClose)} y2={y(prevClose)}
                  stroke="#ffe500" strokeWidth="1" strokeDasharray="5 4" opacity="0.55" />
            <rect x={W - padR + 2} y={y(prevClose) - 8}
                  width={padR - 4} height={16} fill="#1a1000" stroke="#ffe50055" />
            <text x={W - padR + 6} y={y(prevClose) + 4}
                  fontSize="10" fill="#ffe500" fontWeight="700"
                  fontFamily="var(--mono)">
              PC {fmt(prevClose, digits)}
            </text>
          </g>
        )}
        <g>
          <line x1={padL} x2={padL + plotW} y1={y(lastClose)} y2={y(lastClose)}
                stroke={lastClr} strokeWidth="1" strokeDasharray="1 3" opacity="0.9" />
          <rect x={W - padR + 2} y={y(lastClose) - 9}
                width={padR - 4} height={18} fill={lastClr} />
          <text x={W - padR + 6} y={y(lastClose) + 4}
                fontSize="11" fill="#000" fontWeight="800"
                fontFamily="var(--mono)">
            {fmt(lastClose, digits)}
          </text>
        </g>
        {bars.map((c, i) => {
          const open = Number(c.o), high = Number(c.h), low = Number(c.l), close = Number(c.c);
          const up = close >= open;
          const color = up ? "var(--bull)" : "var(--bear)";
          const cx = x(i);
          const top = Math.min(y(open), y(close));
          const h = Math.max(1.5, Math.abs(y(open) - y(close)));
          return (
            <g key={`${c.quote_minute || c.t || i}-${i}`}>
              <line x1={cx} x2={cx} y1={y(high)} y2={y(low)} stroke={color} strokeWidth="1" />
              <rect x={cx - bodyW / 2} y={top} width={bodyW} height={h} fill={color} />
            </g>
          );
        })}
        {[0, Math.floor(bars.length * 0.25), Math.floor(bars.length * 0.5), Math.floor(bars.length * 0.75), bars.length - 1].map((idx, i, arr) => (
          <text key={`t-${i}`}
                x={x(idx)} y={H - 6}
                fontSize="10" fill="#9a6a00"
                fontWeight="700"
                textAnchor={i === 0 ? "start" : i === arr.length - 1 ? "end" : "middle"}
                fontFamily="var(--mono)" letterSpacing="0.05em">
            {fmtClock(bars[idx].quote_minute || bars[idx].quote_timestamp || bars[idx].t)}
          </text>
        ))}
        {hover && (
          <g pointerEvents="none">
            <line x1={hover.cx} x2={hover.cx} y1={padT} y2={padT + plotH}
                  stroke="var(--amber)" strokeWidth="1" strokeDasharray="3 3" opacity="0.6" />
            <line x1={padL} x2={padL + plotW} y1={y(Number(hover.c.c))} y2={y(Number(hover.c.c))}
                  stroke="var(--amber)" strokeWidth="1" strokeDasharray="3 3" opacity="0.4" />
          </g>
        )}
      </svg>
      {hover && (
        <div style={{
          position: "absolute", top: 8, left: 10,
          background: "#000", border: "1px solid var(--amber-dim)",
          padding: "6px 10px", fontSize: 11, fontFamily: "var(--mono)",
          pointerEvents: "none", lineHeight: 1.55,
        }}>
          <div style={{ color: "var(--amber)", letterSpacing: "0.12em", fontSize: 10, textTransform: "uppercase" }}>
            {fmtClock(hover.c.quote_minute || hover.c.quote_timestamp || hover.c.t)}
          </div>
          <div style={{ color: "var(--text-hi)" }}>
            O <b className="strong-num">{fmt(hover.c.o, digits)}</b>&nbsp;&nbsp;
            H <b className="strong-num bull">{fmt(hover.c.h, digits)}</b>
          </div>
          <div style={{ color: "var(--text-hi)" }}>
            L <b className="strong-num bear">{fmt(hover.c.l, digits)}</b>&nbsp;&nbsp;
            C <b className="strong-num">{fmt(hover.c.c, digits)}</b>
          </div>
        </div>
      )}
    </div>
  );
}

function Panel({ title, rhs, children, style, bodyStyle }) {
  return (
    <div className="panel" style={style}>
      {title && (
        <div className="panel-head">
          <span>{title}</span>
          {rhs}
        </div>
      )}
      <div className="panel-body" style={bodyStyle}>{children}</div>
    </div>
  );
}

const DEFAULT_INSTRUMENTS = [
  { symbol: "JP225", label: "JP225", name: "Nikkei 225 Index", asset_class: "index" },
  { symbol: "USA500", label: "USA500", name: "S&P 500 Index", asset_class: "index" },
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
  const [cmd, setCmd] = useState("");
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
  const [lastTick, setLastTick] = useState(null);
  const [tickAge, setTickAge] = useState(0);
  const [tickerQuotes, setTickerQuotes] = useState({});
  const [tickerPaused, setTickerPaused] = useState(false);
  const prevPrice = useRef(null);
  const [flash, setFlash] = useState(null);

  useEffect(() => {
    const t = setInterval(() => {
      setNow(new Date());
      if (lastTick) setTickAge(Math.floor((Date.now() - lastTick) / 1000));
    }, 1000);
    return () => clearInterval(t);
  }, [lastTick]);

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

  useEffect(() => {
    setLive(null);
    prevPrice.current = null;
    setFlash(null);
    setLastTick(null);
    setTickAge(0);
  }, [activeSymbol]);

  const loadLive = useCallback(async () => {
    try {
      const d = await api(`/live/${encodeURIComponent(activeSymbol)}?interval=${encodeURIComponent(chartInterval)}`);
      if (d.error) return;
      if (prevPrice.current !== null && d.price !== prevPrice.current) {
        setFlash(d.price > prevPrice.current ? "up" : "down");
        setTimeout(() => setFlash(null), 700);
      }
      prevPrice.current = d.price;
      setLive(d);
      const quoteMs = d.quote_timestamp ? Date.parse(d.quote_timestamp) : Date.now();
      const safeQuoteMs = Number.isFinite(quoteMs) ? quoteMs : Date.now();
      setLastTick(safeQuoteMs);
      setTickAge(Math.max(0, Math.floor((Date.now() - safeQuoteMs) / 1000)));
    } catch { /* silent */ }
  }, [activeSymbol, chartInterval]);

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

  const loadNeural = useCallback(async () => {
    try { const d = await api("/intelligence/jp225"); setNeuralSchema(d.error ? null : d); } catch { }
  }, []);

  const loadNews = useCallback(async () => {
    try {
      const d = await api(`/news?symbol=${encodeURIComponent(activeSymbol)}`);
      setNews((d.news || []).slice(0, 20));
    } catch { }
  }, [activeSymbol]);

  const loadBriefing = useCallback(async () => {
    try { const d = await api("/briefing"); setBriefing(d.briefing || ""); } catch { }
  }, []);

  const loadTicker = useCallback(async () => {
    try {
      const syms = instruments.map(i => i.symbol);
      const results = await Promise.all(syms.map(s =>
        api(`/live/${encodeURIComponent(s)}?interval=1`).catch(() => null)
      ));
      const next = {};
      results.forEach((r, i) => { if (r && !r.error) next[syms[i]] = r; });
      setTickerQuotes(next);
    } catch { /* silent */ }
  }, [instruments]);

  useEffect(() => { loadLive(); const t = setInterval(loadLive, 2000); return () => clearInterval(t); }, [loadLive]);
  useEffect(() => { loadOverview(); const t = setInterval(loadOverview, 60000); return () => clearInterval(t); }, [loadOverview]);
  useEffect(() => { loadNeural(); const t = setInterval(loadNeural, 60000); return () => clearInterval(t); }, [loadNeural]);
  useEffect(() => { setNews([]); loadNews(); const t = setInterval(loadNews, 30000); return () => clearInterval(t); }, [loadNews]);
  useEffect(() => { loadBriefing(); const t = setInterval(loadBriefing, 300000); return () => clearInterval(t); }, [loadBriefing]);
  useEffect(() => { loadTicker(); const t = setInterval(loadTicker, 15000); return () => clearInterval(t); }, [loadTicker]);

  const activeInstrument = instruments.find(i => i.symbol === activeSymbol) || instruments[0] || DEFAULT_INSTRUMENTS[0];
  const assetClass = live?.asset_class || activeInstrument?.asset_class || "index";
  const priceDigits = digitsFor(assetClass);
  const liveName = live?.name || activeInstrument?.name || activeSymbol;
  const liveLabel = live?.symbol || activeInstrument?.label || activeSymbol;
  const liveSource = live?.source_symbol ? `${live.source || "Source"} ${live.source_symbol}` : `TradingView ${activeInstrument?.source_symbol || activeSymbol}`;
  const chartIntervalLabel = live?.chart_basis?.interval || "1m";
  const isDelayed = Boolean(live?.is_stale || live?.freshness === "delayed");
  const quoteAge = Math.max(Number(live?.quote_age_seconds ?? 0), tickAge);
  const marketContext = live?.market_context || {};
  const drivers = marketContext.drivers || [];
  const sensitivity = marketContext.sensitivity || [];
  const priceColor = live?.change > 0 ? "var(--bull)" : live?.change < 0 ? "var(--bear)" : "var(--amber)";
  const biasColor = bias === "BULLISH" ? "var(--bull)" : bias === "BEARISH" ? "var(--bear)" : "var(--amber)";

  const tickerItems = useMemo(() => instruments.map(inst => {
    const q = tickerQuotes[inst.symbol];
    const d = digitsFor(inst.asset_class);
    const price = q ? Number(q.price) : null;
    const change = q ? Number(q.change_pct) : null;
    const isUp = change > 0, isDn = change < 0;
    const clr = isUp ? "var(--bull)" : isDn ? "var(--bear)" : "var(--muted)";
    const arrow = isUp ? "\u25b2" : isDn ? "\u25bc" : "\u25c6";
    return (
      <span
        key={inst.symbol}
        className="ticker-item"
        onClick={() => setActiveSymbol(inst.symbol)}
        style={{ cursor: "pointer" }}
        title={`Switch to ${inst.label}`}
      >
        <b className="amber">{inst.label}</b>
        <span className="strong-num hi">{price != null ? fmt(price, d) : "\u2014"}</span>
        <span className="strong-num" style={{ color: clr }}>
          {arrow} {change != null ? fmtSigned(change, 2) + "%" : ""}
        </span>
      </span>
    );
  }), [instruments, tickerQuotes]);

  function onCmdKey(e) {
    if (e && e.key !== "Enter") return;
    const sym = cmd.trim().toUpperCase();
    if (instruments.some(i => i.symbol === sym)) {
      setActiveSymbol(sym);
      setCmd("");
    }
  }

  const tzOffset = -new Date().getTimezoneOffset() / 60;

  return (
    <>
      <style>{css}</style>
      <div style={{ minHeight: "100vh", padding: "0", background: "var(--bg)" }}>

        {/* Command bar */}
        <div style={{
          display: "flex", alignItems: "center", gap: 14,
          padding: "6px 14px", borderBottom: "1px solid var(--border)",
          background: "#050505",
        }}>
          <div className="label" style={{ color: "var(--amber)", fontSize: 12, letterSpacing: "0.18em" }}>
            GEOCLAW&nbsp;/&nbsp;TERM
          </div>
          <span className="dim" style={{ fontSize: 11 }}>v1.5</span>
          <div style={{ flex: "0 0 auto", display: "flex", alignItems: "center", gap: 4 }}>
            <span className="dim" style={{ fontSize: 10, letterSpacing: "0.2em" }}>CMD</span>
            <input
              className="cli"
              placeholder="TSLA"
              value={cmd}
              onChange={e => setCmd(e.target.value)}
              onKeyDown={onCmdKey}
              spellCheck={false}
              autoComplete="off"
            />
            <button className="fn" onClick={() => onCmdKey({ key: "Enter" })}>&lt;GO&gt;</button>
          </div>
          <select className="cli" value={activeSymbol} onChange={e => setActiveSymbol(e.target.value)} aria-label="Select asset">
            {instruments.map(inst => (
              <option key={inst.symbol} value={inst.symbol}>{inst.label} · {inst.name}</option>
            ))}
          </select>
          <div style={{ flex: 1 }} />
          <div className="dim" style={{ fontSize: 11 }}>
            <span className="amber">LIVE</span> 2s · <span className="amber">NEWS</span> 30s · <span className="amber">OVRVW</span> 60s
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span className="dot pulse" style={{ background: isDelayed ? "var(--bear)" : "var(--bull)" }} />
            <span className="strong-num hi" style={{ fontSize: 12 }}>{fmtHMS(now)}</span>
            <span className="dim" style={{ fontSize: 10, letterSpacing: "0.1em" }}>UTC{tzOffset >= 0 ? "+" : ""}{tzOffset}</span>
          </div>
        </div>

        {/* Ticker strip */}
        <div className="ticker"
             onMouseEnter={() => setTickerPaused(true)}
             onMouseLeave={() => setTickerPaused(false)}>
          <div className={`ticker-track ${tickerPaused ? "paused" : ""}`}>
            {tickerItems}
            {tickerItems}
          </div>
        </div>

        {err && (
          <div style={{ padding: "4px 12px", color: "var(--bear)", fontSize: 11, background: "#1a0000", borderBottom: "1px solid var(--border)" }}>
            ERR {err}
          </div>
        )}

        <div style={{ padding: 12, display: "grid", gap: 12,
                      gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)" }}>

          {/* LEFT COLUMN */}
          <div style={{ display: "grid", gap: 12 }}>

            {/* Hero quote */}
            <div className={`panel ${flash === "up" ? "flash-up" : flash === "down" ? "flash-dn" : ""}`}
                 style={{ padding: 0 }}>
              <div className="panel-head">
                <span>
                  <span className="amber">{liveLabel}</span>
                  <span className="dim">&nbsp;&nbsp;|&nbsp;&nbsp;</span>
                  {liveName}
                  <span className="dim">&nbsp;&nbsp;·&nbsp;&nbsp;</span>
                  <span className="dim">{liveSource}</span>
                </span>
                <span style={{ color: isDelayed ? "var(--bear)" : "var(--bull)" }}>
                  {isDelayed ? "DELAYED" : "LIVE"} · {quoteAge}s
                </span>
              </div>
              <div style={{
                display: "grid",
                gridTemplateColumns: "minmax(240px, 340px) 1fr auto",
                gap: 12, padding: "14px 16px", alignItems: "start",
              }}>
                {/* Price block */}
                <div>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                    <span className="strong-num" style={{ fontSize: 44, color: priceColor, lineHeight: 1, letterSpacing: "-0.02em" }}>
                      {live ? fmt(live.price, priceDigits) : "\u2014"}
                    </span>
                    {live && (
                      <span className="strong-num" style={{ fontSize: 16, color: priceColor }}>
                        {live.change >= 0 ? "\u25b2" : "\u25bc"} {fmt(Math.abs(live.change), priceDigits)}&nbsp;
                        <span style={{ fontSize: 13 }}>
                          ({live.change_pct >= 0 ? "+" : ""}{(live.change_pct ?? 0).toFixed(2)}%)
                        </span>
                      </span>
                    )}
                  </div>
                  {live && (
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(2, auto)", gap: "4px 18px",
                                  fontSize: 11, marginTop: 12, color: "var(--text)" }}>
                      <div className="kv"><span className="k">Open</span><span className="v strong-num">{fmt(live.open, priceDigits)}</span></div>
                      <div className="kv"><span className="k">P.Cls</span><span className="v strong-num">{fmt(live.prev_close, priceDigits)}</span></div>
                      <div className="kv"><span className="k">High</span><span className="v strong-num bull">{fmt(live.day_high, priceDigits)}</span></div>
                      <div className="kv"><span className="k">Low</span><span className="v strong-num bear">{fmt(live.day_low, priceDigits)}</span></div>
                      {live.bid != null && <div className="kv"><span className="k">Bid</span><span className="v strong-num">{fmt(live.bid, priceDigits)}</span></div>}
                      {live.ask != null && <div className="kv"><span className="k">Ask</span><span className="v strong-num">{fmt(live.ask, priceDigits)}</span></div>}
                      <div className="kv"><span className="k">Sess.</span><span className="v" style={{ color: "var(--amber)" }}>{live.session || "\u2014"}</span></div>
                      <div className="kv"><span className="k">Type</span><span className="v dim">{live.market_type || "\u2014"}</span></div>
                    </div>
                  )}
                </div>

                {/* Candle */}
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <span className="dim" style={{ fontSize: 10, letterSpacing: "0.12em" }}>
                      {`INTRADAY · ${chartIntervalLabel} \u00d7 60`}
                    </span>
                    <div style={{ display: "flex", gap: 4 }}>
                      {[["1", "1m"], ["30", "30m"], ["60", "1h"]].map(([val, lbl]) => (
                        <button key={val}
                                className={`fn ${chartInterval === val ? "on" : ""}`}
                                onClick={() => setChartInterval(val)}>
                          {lbl}
                        </button>
                      ))}
                    </div>
                  </div>
                  <CandleChart
                    candles={live?.candles}
                    prevClose={Number(live?.prev_close)}
                    digits={priceDigits}
                    height={300}
                  />
                </div>

                {/* Bias + counters */}
                <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 120 }}>
                  <div style={{
                    textAlign: "center", padding: "10px 14px",
                    border: `1px solid ${biasColor}`, background: `${biasColor}14`,
                  }}>
                    <div className="label" style={{ marginBottom: 4 }}>Market Bias</div>
                    <div className="strong-num" style={{ fontSize: 20, color: biasColor, letterSpacing: "0.06em" }}>
                      {bias}
                    </div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                    <div style={{ textAlign: "center", padding: "6px 8px", border: "1px solid #22e67044", background: "#22e6700a" }}>
                      <div className="label" style={{ color: "var(--bull)", fontSize: 9 }}>BULL</div>
                      <div className="strong-num bull" style={{ fontSize: 22 }}>{bullCount}</div>
                    </div>
                    <div style={{ textAlign: "center", padding: "6px 8px", border: "1px solid #ff3d3d44", background: "#ff3d3d0a" }}>
                      <div className="label" style={{ color: "var(--bear)", fontSize: 9 }}>BEAR</div>
                      <div className="strong-num bear" style={{ fontSize: 22 }}>{bearCount}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Why moving + sensitivity */}
            {live?.market_context && (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <Panel title={`WHY ${liveLabel} IS MOVING TODAY`}>
                  <div style={{ fontSize: 12, lineHeight: 1.55, marginBottom: 10, color: "var(--text-hi)" }}>
                    {marketContext.summary}
                  </div>
                  <div style={{ display: "grid", gap: 6 }}>
                    {drivers.map((d, idx) => {
                      const impactColor = d.impact === "bullish" ? "var(--bull)" : d.impact === "bearish" ? "var(--bear)" : "var(--amber)";
                      return (
                        <div key={idx} style={{ padding: "6px 8px", border: "1px solid var(--border)", background: "#000" }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 2 }}>
                            <b style={{ fontSize: 12, color: "var(--text-hi)" }}>{d.label}</b>
                            <span className="label" style={{ color: impactColor, fontSize: 10 }}>{d.impact}</span>
                          </div>
                          <div className="dim" style={{ fontSize: 11, lineHeight: 1.4 }}>{d.why}</div>
                        </div>
                      );
                    })}
                  </div>
                </Panel>
                <Panel title="SENSITIVITY MAP">
                  <div style={{ display: "grid", gap: 6 }}>
                    {sensitivity.map((s, idx) => {
                      const val = Math.max(-100, Math.min(100, Number(s.score || 0)));
                      const positive = val >= 0;
                      const barWidth = Math.abs(val) / 2;
                      return (
                        <div key={idx}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11, marginBottom: 2 }}>
                            <b style={{ color: "var(--text-hi)" }}>{s.factor}</b>
                            <span className="strong-num" style={{ color: positive ? "var(--bull)" : "var(--bear)" }}>
                              {positive ? "+" : ""}{val}
                            </span>
                          </div>
                          <div style={{ height: 5, background: "#0a0a0a", border: "1px solid var(--border)", position: "relative", overflow: "hidden" }}>
                            <div style={{
                              position: "absolute",
                              left: "50%", top: 0, bottom: 0, width: 1, background: "var(--border-hi)",
                            }} />
                            <div style={{
                              width: `${barWidth}%`,
                              marginLeft: positive ? "50%" : `${50 - barWidth}%`,
                              height: "100%",
                              background: positive ? "var(--bull)" : "var(--bear)",
                              opacity: 0.9,
                            }} />
                          </div>
                          <div className="dim" style={{ fontSize: 10, marginTop: 2 }}>{s.why}</div>
                        </div>
                      );
                    })}
                  </div>
                </Panel>
              </div>
            )}

            {/* Headlines table */}
            <Panel title={`HEADLINES · ${liveLabel}`}
                   rhs={<span className="dim">{news.length} ITEMS · 30s REFRESH</span>}>
              {news.length === 0
                ? <div className="dim" style={{ fontSize: 12 }}>{"NO HEADLINES. AWAITING FEED\u2026"}</div>
                : (
                  <div style={{ maxHeight: 320, overflowY: "auto", border: "1px solid var(--border)" }}>
                    <table className="tbl">
                      <thead>
                        <tr>
                          <th style={{ width: 74 }}>TIME</th>
                          <th style={{ width: 140 }}>SOURCE</th>
                          <th>HEADLINE</th>
                        </tr>
                      </thead>
                      <tbody>
                        {news.map(n => {
                          const d = n.ts ? new Date(n.ts) : null;
                          const timeLabel = d && Number.isFinite(d.getTime())
                            ? d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })
                            : "\u2014";
                          return (
                            <tr key={n.id}>
                              <td><span className="amber strong-num">{timeLabel}</span></td>
                              <td className="dim" style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 140 }}>
                                {n.source}
                              </td>
                              <td>
                                <a href={n.url} target="_blank" rel="noreferrer" style={{ color: "var(--text-hi)" }}>
                                  {n.headline}
                                </a>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
            </Panel>
          </div>

          {/* RIGHT COLUMN */}
          <div style={{ display: "grid", gap: 12, alignContent: "start" }}>

            {neuralSchema && (
              <Panel title="NEURAL INTELLIGENCE SCHEMA · JP225"
                     rhs={
                       <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                         <span className="dim">{neuralSchema.llm_provider} · {neuralSchema.elapsed_seconds}s · {neuralSchema.cached ? "cached" : "live"}</span>
                         <span style={{
                           padding: "2px 8px",
                           background: neuralSchema.bias === "BULLISH" ? "var(--bull)" : neuralSchema.bias === "BEARISH" ? "var(--bear)" : "var(--amber)",
                           color: "#000", fontWeight: 800, letterSpacing: "0.08em", fontSize: 11,
                         }}>
                           {neuralSchema.bias} {neuralSchema.confidence}%
                         </span>
                       </span>
                     }>
                <div style={{ fontSize: 12, lineHeight: 1.55, marginBottom: 10, color: "var(--text-hi)" }}>
                  <span className="label">Thesis</span>&nbsp; {neuralSchema.short_thesis}
                </div>
                {neuralSchema.trade_note && (
                  <div style={{
                    fontSize: 11, lineHeight: 1.5, color: "var(--text)",
                    padding: "6px 10px", background: "#0a0800", borderLeft: "3px solid var(--amber)",
                    marginBottom: 10,
                  }}>
                    {neuralSchema.trade_note}
                  </div>
                )}
                <div className="label" style={{ marginBottom: 6 }}>
                  FACTOR SCORES · COMPOSITE {neuralSchema.composite_score > 0 ? "+" : ""}{neuralSchema.composite_score}
                </div>
                <div style={{ display: "grid", gap: 4 }}>
                  {(neuralSchema.factors || []).map(f => {
                    const pct = Math.min(100, Math.abs(f.score));
                    const bull = f.direction === "bullish";
                    const bear = f.direction === "bearish";
                    const c = bull ? "var(--bull)" : bear ? "var(--bear)" : "var(--amber)";
                    return (
                      <div key={f.id}>
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11 }}>
                          <span className="hi">{f.label}</span>
                          <span className="strong-num" style={{ color: c }}>{f.score > 0 ? "+" : ""}{f.score}</span>
                        </div>
                        <div style={{ height: 3, background: "#0a0a0a", border: "1px solid var(--border)", overflow: "hidden" }}>
                          <div style={{ width: `${pct}%`, height: "100%", background: c }} />
                        </div>
                      </div>
                    );
                  })}
                </div>

                <div style={{ display: "grid", gap: 6, marginTop: 12 }}>
                  <div style={{ padding: "6px 10px", border: "1px solid #22e67044", background: "#22e6700a" }}>
                    <div className="label" style={{ color: "var(--bull)" }}>Key Driver</div>
                    <div className="hi strong-num" style={{ fontSize: 12 }}>{neuralSchema.key_driver}</div>
                  </div>
                  <div style={{ padding: "6px 10px", border: "1px solid #ff3d3d44", background: "#ff3d3d0a" }}>
                    <div className="label" style={{ color: "var(--bear)" }}>Risk Factor</div>
                    <div className="hi strong-num" style={{ fontSize: 12 }}>{neuralSchema.risk_factor}</div>
                  </div>
                  {neuralSchema.news_signal && (
                    <div style={{ padding: "6px 10px", border: "1px solid var(--border)" }}>
                      <div className="label">NLP · {neuralSchema.news_signal.headlines_scanned} HDLNS</div>
                      <div style={{ display: "flex", gap: 14, fontSize: 12, marginTop: 2 }}>
                        <span className="bull strong-num">{"\u25b2 "}{neuralSchema.news_signal.bull_score}</span>
                        <span className="bear strong-num">{"\u25bc "}{neuralSchema.news_signal.bear_score}</span>
                      </div>
                    </div>
                  )}
                </div>
              </Panel>
            )}

            <Panel title="AI BRIEFING">
              <div style={{ fontSize: 12, lineHeight: 1.6, maxHeight: 260, overflowY: "auto", whiteSpace: "pre-wrap", color: "var(--text)" }}>
                {briefing || <span className="dim">{"LOADING BRIEFING\u2026"}</span>}
              </div>
            </Panel>

            <Panel title={`SIGNALS · ${signals.length}`}>
              {signals.length === 0
                ? <div className="dim" style={{ fontSize: 12 }}>NO SIGNALS YET.</div>
                : (
                  <div style={{ display: "grid", gap: 3, maxHeight: 260, overflowY: "auto" }}>
                    {signals.map(s => {
                      const dir = (s.direction || "").toUpperCase();
                      const isBull = ["BUY","BULLISH"].includes(dir);
                      const isBear = ["SELL","BEARISH"].includes(dir);
                      const c = isBull ? "var(--bull)" : isBear ? "var(--bear)" : "var(--amber)";
                      return (
                        <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 6, padding: "3px 6px", background: "#000", border: "1px solid var(--border)" }}>
                          <div className="strong-num" style={{ width: 28, fontSize: 11, color: c, flexShrink: 0 }}>
                            {isBull ? "BUY" : isBear ? "SELL" : "\u2014"}
                          </div>
                          <div style={{ flex: 1, fontSize: 11, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {s.signal_name}
                          </div>
                          <div style={{ width: 46, height: 3, background: "#0a0a0a", border: "1px solid var(--border)", overflow: "hidden", flexShrink: 0 }}>
                            <div style={{ width: `${Math.min(100, s.confidence || 0)}%`, height: "100%", background: c }} />
                          </div>
                          <div className="strong-num dim" style={{ fontSize: 10, minWidth: 28, textAlign: "right" }}>
                            {Math.round(s.confidence || 0)}%
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
            </Panel>
          </div>
        </div>

        {/* Footer status strip */}
        <div style={{
          display: "flex", alignItems: "center", gap: 18,
          padding: "4px 14px", borderTop: "1px solid var(--border)",
          background: "#050505", fontSize: 10, letterSpacing: "0.1em",
        }}>
          <span className="amber">GEOCLAW TERMINAL</span>
          <span className="dim">© 2026</span>
          <span className="dim">|</span>
          <span className="dim">LIVE&nbsp;2s</span>
          <span className="dim">NEWS&nbsp;30s</span>
          <span className="dim">OVERVIEW&nbsp;60s</span>
          <span className="dim">BRIEFING&nbsp;5m</span>
          <span style={{ flex: 1 }} />
          <span className="dim">{liveSource}</span>
          <span className="dim">|</span>
          <span className="amber">{fmtHMS(now)}</span>
        </div>
      </div>
    </>
  );
}

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

function CandleChart({ candles }) {
  if (!candles || candles.length < 2) return null;
  const bars = candles.slice(-60).filter(c => [c.o, c.h, c.l, c.c].every(v => Number.isFinite(Number(v))));
  if (bars.length < 2) return null;
  const lows = bars.map(c => Number(c.l));
  const highs = bars.map(c => Number(c.h));
  const min = Math.min(...lows);
  const max = Math.max(...highs);
  const range = max - min || 1;
  const W = 420, H = 92;
  const gap = 2;
  const step = W / bars.length;
  const bodyW = Math.max(2, Math.min(7, step - gap));
  const y = (v) => H - ((Number(v) - min) / range) * (H - 10) - 5;
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ display: "block", height: 92 }}>
      <line x1="0" x2={W} y1={y(bars[bars.length - 1].c)} y2={y(bars[bars.length - 1].c)} stroke="#334155" strokeWidth="1" strokeDasharray="4 4" opacity="0.6" />
      {bars.map((c, i) => {
        const open = Number(c.o), high = Number(c.h), low = Number(c.l), close = Number(c.c);
        const up = close >= open;
        const color = up ? "var(--bull)" : "var(--bear)";
        const cx = i * step + step / 2;
        const top = Math.min(y(open), y(close));
        const height = Math.max(1.5, Math.abs(y(open) - y(close)));
        return (
          <g key={`${c.quote_minute || c.t}-${i}`}>
            <line x1={cx} x2={cx} y1={y(high)} y2={y(low)} stroke={color} strokeWidth="1" opacity="0.9" />
            <rect x={cx - bodyW / 2} y={top} width={bodyW} height={height} rx="0.8" fill={color} opacity="0.82" />
          </g>
        );
      })}
    </svg>
  );
}

function Card({ children, style }) {
  return <div style={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 12, padding: 16, ...style }}>{children}</div>;
}

function Label({ children }) {
  return <div style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>{children}</div>;
}

export default function App() {
  const [live, setLive] = useState(null);
  const [chartInterval, setChartInterval] = useState("1");
  const [bias, setBias] = useState("—");
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

  // Live JP225 — refresh every 2s for intraminute chart accuracy
  const loadLive = useCallback(async () => {
    try {
      const d = await api(`/live/jp225?interval=${encodeURIComponent(chartInterval)}`);
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
  }, [chartInterval]);

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

  // News — refresh every 5 min
  const loadNews = useCallback(async () => {
    try { const d = await api("/news"); setNews((d.news || []).slice(0, 12)); } catch { }
  }, []);

  // Briefing — refresh every 5 min
  const loadBriefing = useCallback(async () => {
    try { const d = await api("/briefing"); setBriefing(d.briefing || ""); } catch { }
  }, []);

  useEffect(() => { loadLive(); const t = setInterval(loadLive, 2000); return () => clearInterval(t); }, [loadLive]);
  useEffect(() => { loadOverview(); const t = setInterval(loadOverview, 60000); return () => clearInterval(t); }, [loadOverview]);
  useEffect(() => { loadNeural(); const t = setInterval(loadNeural, 60000); return () => clearInterval(t); }, [loadNeural]);
  useEffect(() => { loadNews(); const t = setInterval(loadNews, 300000); return () => clearInterval(t); }, [loadNews]);
  useEffect(() => { loadBriefing(); const t = setInterval(loadBriefing, 300000); return () => clearInterval(t); }, [loadBriefing]);

  const biasColor = bias === "BULLISH" ? "var(--bull)" : bias === "BEARISH" ? "var(--bear)" : "var(--neutral)";
  const priceColor = live?.direction === "up" ? "var(--bull)" : live?.direction === "down" ? "var(--bear)" : "var(--text)";
  const flashBg = flash === "up" ? "#22c55e22" : flash === "down" ? "#ef444422" : "transparent";
  const liveName = live?.name || "Nikkei 225 proxy";
  const liveSource = live?.source_symbol ? `${live.source || "Source"} ${live.source_symbol}` : "TradingView FOREXCOM-JP225";
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
          <div>
            <div style={{ fontSize: 11, color: "var(--muted)", letterSpacing: "0.1em", textTransform: "uppercase" }}>GeoClaw Intelligence</div>
            <h1 style={{ margin: "2px 0 0", fontSize: 20, fontWeight: 700 }}>{liveName} · {isDelayed ? "Delayed" : "Live"}</h1>
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
                  {live ? Number(live.price).toLocaleString("en-GB", { maximumFractionDigits: 0 }) : "—"}
                </span>
                {live && (
                  <span style={{ fontSize: 20, fontWeight: 600, color: priceColor }}>
                    {live.change >= 0 ? "▲" : "▼"} {Math.abs(live.change).toLocaleString("en-GB", { maximumFractionDigits: 0 })} ({live.change_pct >= 0 ? "+" : ""}{live.change_pct.toFixed(2)}%)
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
              <CandleChart candles={live?.candles} />
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
              <Label>Why JP225 is positive today</Label>
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

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

function Sparkline({ candles, color }) {
  if (!candles || candles.length < 2) return null;
  const closes = candles.map(c => c.c);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const range = max - min || 1;
  const W = 320, H = 60;
  const pts = closes.map((v, i) => {
    const x = (i / (closes.length - 1)) * W;
    const y = H - ((v - min) / range) * H;
    return `${x},${y}`;
  }).join(" ");
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ display: "block", height: 60 }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" />
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
  const [bias, setBias] = useState("—");
  const [bullCount, setBullCount] = useState(0);
  const [bearCount, setBearCount] = useState(0);
  const [signals, setSignals] = useState([]);
  const [news, setNews] = useState([]);
  const [briefing, setBriefing] = useState("");
  const [err, setErr] = useState("");
  const [now, setNow] = useState(new Date());
  const prevPrice = useRef(null);
  const [flash, setFlash] = useState(null); // "up" | "down"

  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);

  // Live JP225 — refresh every 15s
  const loadLive = useCallback(async () => {
    try {
      const d = await api("/live/jp225");
      if (d.error) return;
      if (prevPrice.current !== null && d.price !== prevPrice.current) {
        setFlash(d.price > prevPrice.current ? "up" : "down");
        setTimeout(() => setFlash(null), 800);
      }
      prevPrice.current = d.price;
      setLive(d);
    } catch { /* silent */ }
  }, []);

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

  // News — refresh every 5 min
  const loadNews = useCallback(async () => {
    try { const d = await api("/news"); setNews((d.news || []).slice(0, 12)); } catch { }
  }, []);

  // Briefing — refresh every 5 min
  const loadBriefing = useCallback(async () => {
    try { const d = await api("/briefing"); setBriefing(d.briefing || ""); } catch { }
  }, []);

  useEffect(() => { loadLive(); const t = setInterval(loadLive, 15000); return () => clearInterval(t); }, [loadLive]);
  useEffect(() => { loadOverview(); const t = setInterval(loadOverview, 60000); return () => clearInterval(t); }, [loadOverview]);
  useEffect(() => { loadNews(); const t = setInterval(loadNews, 300000); return () => clearInterval(t); }, [loadNews]);
  useEffect(() => { loadBriefing(); const t = setInterval(loadBriefing, 300000); return () => clearInterval(t); }, [loadBriefing]);

  const biasColor = bias === "BULLISH" ? "var(--bull)" : bias === "BEARISH" ? "var(--bear)" : "var(--neutral)";
  const priceColor = live?.direction === "up" ? "var(--bull)" : live?.direction === "down" ? "var(--bear)" : "var(--text)";
  const flashBg = flash === "up" ? "#22c55e22" : flash === "down" ? "#ef444422" : "transparent";

  return (
    <>
      <style>{css}</style>
      <div style={{ maxWidth: 1280, margin: "0 auto", padding: "16px 20px" }}>

        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
          <div>
            <div style={{ fontSize: 11, color: "var(--muted)", letterSpacing: "0.1em", textTransform: "uppercase" }}>GeoClaw Intelligence</div>
            <h1 style={{ margin: "2px 0 0", fontSize: 20, fontWeight: 700 }}>Nikkei 225 CFD · Live</h1>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ fontSize: 13, color: "var(--muted)" }}>
              {now.toLocaleString("en-GB", { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" })}
            </div>
            <div style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--bull)" }} className="pulse" />
          </div>
        </div>

        {err && <div style={{ color: "var(--bear)", fontSize: 12, marginBottom: 12 }}>{err}</div>}

        {/* Live price hero */}
        <Card style={{ marginBottom: 16, transition: "background 0.4s", background: flash ? flashBg : "var(--card)" }}>
          <div style={{ display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 24, alignItems: "center" }}>
            <div>
              <Label>Nikkei 225 CFD · ^N225</Label>
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
                </div>
              )}
            </div>
            <div style={{ minWidth: 0 }}>
              <Sparkline candles={live?.candles} color={priceColor} />
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
          Price updates every 15s · Signals every 60s · GeoClaw
        </div>
      </div>
    </>
  );
}

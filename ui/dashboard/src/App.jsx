import React, { useCallback, useEffect, useState } from "react";

const API_BASE = "http://127.0.0.1:8000/api";

const css = `
:root {
  --bg: #0d1117; --card: #161b22; --border: #30363d;
  --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
  --bull: #3fb950; --bear: #f85149; --neutral: #d29922;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: ui-sans-serif, system-ui, sans-serif; background: var(--bg); color: var(--text); }
`;

async function api(path) {
  const r = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

function Card({ title, children, style }) {
  return (
    <div style={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 12, padding: 16, ...style }}>
      {title && <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10, textTransform: "uppercase", letterSpacing: 1 }}>{title}</div>}
      {children}
    </div>
  );
}

function Tag({ color, children }) {
  return <span style={{ padding: "2px 8px", borderRadius: 4, background: color + "22", color, fontSize: 12, fontWeight: 700 }}>{children}</span>;
}

export default function App() {
  const [price, setPrice] = useState(null);
  const [bias, setBias] = useState("NEUTRAL");
  const [signals, setSignals] = useState([]);
  const [news, setNews] = useState([]);
  const [briefing, setBriefing] = useState("");
  const [lastRun, setLastRun] = useState("");
  const [err, setErr] = useState("");
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const load = useCallback(async () => {
    setErr("");
    try {
      const [priceData, overview, newsData] = await Promise.all([
        api("/prices"),
        api("/dashboard/overview"),
        api("/news"),
      ]);

      const jp = (priceData.prices || [])[0];
      if (jp) setPrice(jp);

      const ov = overview || {};
      setBias((ov.market_bias?.label || "NEUTRAL").toUpperCase());
      setSignals((ov.signals || []).slice(0, 20));
      setLastRun(ov.last_run_at || "");
      setNews((newsData.news || []).slice(0, 15));
    } catch (e) {
      setErr(String(e.message || e));
    }
  }, []);

  const loadBriefing = useCallback(async () => {
    try {
      const d = await api("/briefing");
      setBriefing(d.briefing || d.text || "");
    } catch {
      setBriefing("");
    }
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);
  useEffect(() => { loadBriefing(); const t = setInterval(loadBriefing, 120000); return () => clearInterval(t); }, [loadBriefing]);

  const biasColor = bias === "BULLISH" ? "var(--bull)" : bias === "BEARISH" ? "var(--bear)" : "var(--neutral)";

  const bullCount = signals.filter(s => ["BULLISH","BUY"].includes((s.direction || "").toUpperCase())).length;
  const bearCount = signals.filter(s => ["BEARISH","SELL"].includes((s.direction || "").toUpperCase())).length;

  return (
    <>
      <style>{css}</style>
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "20px 16px" }}>

        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>GeoClaw · Japan 225 CFD</h1>
            <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 4 }}>
              {now.toLocaleString("en-GB", { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" })}
              {lastRun && <span style={{ marginLeft: 16 }}>Last agent run: {new Date(lastRun).toLocaleTimeString()}</span>}
            </div>
          </div>
          <button onClick={load} style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--card)", color: "var(--text)", cursor: "pointer", fontSize: 13 }}>
            ↻ Refresh
          </button>
        </div>

        {err && <div style={{ color: "var(--bear)", padding: "10px 14px", border: "1px solid var(--bear)", borderRadius: 8, marginBottom: 16, fontSize: 13 }}>{err}</div>}

        {/* Top row: price + bias + signal summary */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 12, marginBottom: 16 }}>
          <Card title="JP225 Price">
            <div style={{ fontSize: 30, fontWeight: 700 }}>
              {price?.price ? Number(price.price).toLocaleString("en-GB", { maximumFractionDigits: 0 }) : "—"}
            </div>
            <div style={{ fontSize: 13, marginTop: 4 }}>
              <Tag color={price?.direction === "up" ? "var(--bull)" : price?.direction === "down" ? "var(--bear)" : "var(--muted)"}>
                {price?.direction?.toUpperCase() || "—"}
              </Tag>
            </div>
          </Card>

          <Card title="Market Bias">
            <div style={{ fontSize: 28, fontWeight: 700, color: biasColor }}>{bias}</div>
          </Card>

          <Card title="Bullish Signals">
            <div style={{ fontSize: 30, fontWeight: 700, color: "var(--bull)" }}>{bullCount}</div>
          </Card>

          <Card title="Bearish Signals">
            <div style={{ fontSize: 30, fontWeight: 700, color: "var(--bear)" }}>{bearCount}</div>
          </Card>
        </div>

        {/* Middle: briefing + news */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
          <Card title="AI Briefing">
            <div style={{ fontSize: 13, lineHeight: 1.7, color: "var(--text)", maxHeight: 320, overflowY: "auto", whiteSpace: "pre-wrap" }}>
              {briefing || <span style={{ color: "var(--muted)" }}>Generating briefing…</span>}
            </div>
          </Card>

          <Card title="Latest News">
            <div style={{ display: "flex", flexDirection: "column", gap: 10, maxHeight: 320, overflowY: "auto" }}>
              {news.length === 0 && <div style={{ color: "var(--muted)", fontSize: 13 }}>No news yet.</div>}
              {news.map(n => (
                <div key={n.id} style={{ borderBottom: "1px solid var(--border)", paddingBottom: 10 }}>
                  <a href={n.url} target="_blank" rel="noreferrer" style={{ color: "var(--accent)", fontSize: 13, fontWeight: 600, textDecoration: "none", lineHeight: 1.4, display: "block" }}>
                    {n.headline}
                  </a>
                  <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 4 }}>
                    {n.source} · {n.ts ? new Date(n.ts).toLocaleTimeString() : ""}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>

        {/* Signals table */}
        <Card title="Signals">
          {signals.length === 0 && <div style={{ color: "var(--muted)", fontSize: 13 }}>No signals yet. Run the agent to generate signals.</div>}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {signals.map(s => {
              const dir = (s.direction || "").toUpperCase();
              const color = ["BULLISH","BUY"].includes(dir) ? "var(--bull)" : ["BEARISH","SELL"].includes(dir) ? "var(--bear)" : "var(--muted)";
              return (
                <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 12px", background: "#0d1117", borderRadius: 8, border: "1px solid var(--border)" }}>
                  <Tag color={color}>{dir || "HOLD"}</Tag>
                  <div style={{ flex: 1, fontSize: 13, fontWeight: 600 }}>{s.signal_name}</div>
                  <div style={{ width: 80, height: 6, background: "#21262d", borderRadius: 3, overflow: "hidden" }}>
                    <div style={{ width: `${Math.min(100, Number(s.confidence) || 0)}%`, height: "100%", background: color }} />
                  </div>
                  <div style={{ fontSize: 12, color: "var(--muted)", minWidth: 36, textAlign: "right" }}>{Math.round(s.confidence || 0)}%</div>
                </div>
              );
            })}
          </div>
        </Card>

        <div style={{ marginTop: 16, fontSize: 12, color: "var(--muted)", textAlign: "center" }}>
          Refreshes every 30s · GeoClaw Intelligence
        </div>
      </div>
    </>
  );
}

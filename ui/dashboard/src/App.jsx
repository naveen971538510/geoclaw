import React, { useCallback, useEffect, useMemo, useState } from "react";

const API_BASE = "http://127.0.0.1:8001/api";

const cssVars = `
:root {
  --bg: #0d1117;
  --card: #161b22;
  --border: #30363d;
  --text: #e6edf3;
  --muted: #8b949e;
  --accent: #58a6ff;
  --bull: #3fb950;
  --bear: #f85149;
  --neutral: #d29922;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: ui-sans-serif, system-ui, sans-serif; background: var(--bg); color: var(--text); }
`;

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

async function fetchJson(path) {
  const r = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} HTTP ${r.status}`);
  return r.json();
}

function Card({ title, children, style }) {
  return (
    <div
      style={{
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 12,
        padding: 16,
        ...style,
      }}
    >
      {title && (
        <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 8 }}>{title}</div>
      )}
      {children}
    </div>
  );
}

function dirEmoji(d) {
  const x = (d || "").toUpperCase();
  if (x === "BEARISH") return "🔴";
  if (x === "BULLISH") return "🟢";
  return "⚪";
}

export default function App() {
  const now = useClock();
  const [signals, setSignals] = useState([]);
  const [macro, setMacro] = useState([]);
  const [charts, setCharts] = useState([]);
  const [briefing, setBriefing] = useState("");
  const [err, setErr] = useState("");
  const [tickersInput, setTickersInput] = useState("AAPL, MSFT");
  const [portfolio, setPortfolio] = useState(null);
  const [loadingPf, setLoadingPf] = useState(false);

  const load = useCallback(async () => {
    setErr("");
    try {
      const [s, m, c] = await Promise.all([
        fetchJson("/signals"),
        fetchJson("/macro"),
        fetchJson("/charts"),
      ]);
      setSignals(s.signals || []);
      setMacro(m.macro || []);
      setCharts(c.charts || []);
    } catch (e) {
      setErr(String(e.message || e));
    }
  }, []);

  const loadBriefing = useCallback(async () => {
    try {
      const b = await fetchJson("/briefing");
      setBriefing(b.briefing || "");
    } catch {
      setBriefing("(Briefing unavailable — check GROQ_API_KEY and DATABASE_URL)");
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 60000);
    return () => clearInterval(id);
  }, [load]);

  useEffect(() => {
    loadBriefing();
    const id = setInterval(loadBriefing, 300000);
    return () => clearInterval(id);
  }, [loadBriefing]);

  const metrics = useMemo(() => {
    const todayStart = new Date();
    todayStart.setUTCHours(0, 0, 0, 0);
    const todaySig = signals.filter((s) => s.ts && new Date(s.ts) >= todayStart);
    const count = todaySig.length || signals.length;
    const confs = signals.map((s) => Number(s.confidence) || 0);
    const avg = confs.length ? confs.reduce((a, b) => a + b, 0) / confs.length : 0;
    let bull = 0,
      bear = 0,
      w = 0;
    signals.forEach((s) => {
      const c = Number(s.confidence) || 0;
      const d = (s.direction || "").toUpperCase();
      if (d === "BULLISH") bull += c;
      if (d === "BEARISH") bear += c;
      w += c;
    });
    let bias = "NEUTRAL";
    if (w > 0) {
      if (bear > bull * 1.15) bias = "BEARISH";
      else if (bull > bear * 1.15) bias = "BULLISH";
    }
    const bears = signals.filter((s) => (s.direction || "").toUpperCase() === "BEARISH");
    const topRisk = bears.sort((a, b) => (b.confidence || 0) - (a.confidence || 0))[0];
    return {
      count,
      avg,
      bias,
      topRisk: topRisk?.signal_name || "—",
    };
  }, [signals]);

  async function analysePortfolio() {
    setLoadingPf(true);
    setErr("");
    try {
      const list = tickersInput
        .split(/[,\s]+/)
        .map((t) => t.trim().toUpperCase())
        .filter(Boolean);
      const r = await fetch(`${API_BASE}/portfolio`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(list),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || r.status);
      setPortfolio(j);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setLoadingPf(false);
    }
  }

  const biasColor =
    metrics.bias === "BULLISH"
      ? "var(--bull)"
      : metrics.bias === "BEARISH"
        ? "var(--bear)"
        : "var(--neutral)";

  return (
    <>
      <style>{cssVars}</style>
      <div style={{ maxWidth: 1100, margin: "0 auto", padding: 24 }}>
        <header style={{ marginBottom: 24 }}>
          <h1 style={{ margin: "0 0 8px", fontSize: 26 }}>GeoClaw — Economic Intelligence</h1>
          <div style={{ color: "var(--muted)", fontSize: 14 }}>
            {now.toLocaleString(undefined, {
              weekday: "long",
              year: "numeric",
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            })}
          </div>
        </header>

        {err && (
          <div
            style={{
              color: "#f85149",
              marginBottom: 16,
              padding: 12,
              border: "1px solid #f85149",
              borderRadius: 8,
            }}
          >
            {err}
          </div>
        )}

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: 12,
            marginBottom: 24,
          }}
        >
          <Card title="Signal count (today / window)">
            <div style={{ fontSize: 28, fontWeight: 700 }}>{metrics.count}</div>
          </Card>
          <Card title="Avg confidence">
            <div style={{ fontSize: 28, fontWeight: 700 }}>{metrics.avg.toFixed(1)}%</div>
          </Card>
          <Card title="Market bias">
            <div style={{ fontSize: 22, fontWeight: 700, color: biasColor }}>{metrics.bias}</div>
          </Card>
          <Card title="Top risk factor">
            <div style={{ fontSize: 15, fontWeight: 600 }}>{metrics.topRisk}</div>
          </Card>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 24 }}>
          <Card title="AI briefing (refreshes periodically)">
            <div style={{ fontSize: 14, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{briefing || "Loading…"}</div>
          </Card>
          <Card title="Portfolio analyser">
            <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 8 }}>
              Comma-separated tickers
            </div>
            <input
              value={tickersInput}
              onChange={(e) => setTickersInput(e.target.value)}
              style={{
                width: "100%",
                padding: 10,
                borderRadius: 8,
                border: "1px solid var(--border)",
                background: "#0d1117",
                color: "var(--text)",
                marginBottom: 10,
              }}
            />
            <button
              type="button"
              onClick={analysePortfolio}
              disabled={loadingPf}
              style={{
                padding: "10px 18px",
                borderRadius: 8,
                border: "none",
                background: "var(--accent)",
                color: "#0d1117",
                fontWeight: 700,
                cursor: loadingPf ? "wait" : "pointer",
              }}
            >
              {loadingPf ? "Analysing…" : "Analyse"}
            </button>
            {portfolio && (
              <div style={{ marginTop: 14, fontSize: 14 }}>
                <div style={{ color: "var(--muted)", marginBottom: 6 }}>
                  Macro bias hint: {Number(portfolio.macro_bias_hint || 0).toFixed(2)}
                </div>
                {(portfolio.holdings || []).map((h) => (
                  <div
                    key={h.ticker}
                    style={{
                      borderTop: "1px solid var(--border)",
                      paddingTop: 8,
                      marginTop: 8,
                    }}
                  >
                    <b>{h.ticker}</b> — risk <span style={{ color: "var(--accent)" }}>{h.risk_score}</span>
                    <div style={{ color: "var(--muted)", marginTop: 4 }}>{h.recommendation}</div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        <h2 style={{ fontSize: 18, margin: "24px 0 12px" }}>Live signals</h2>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {signals.length === 0 && (
            <div style={{ color: "var(--muted)" }}>No signals in the last 24h.</div>
          )}
          {signals.map((s) => (
            <Card key={s.id}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
                <span style={{ fontSize: 22 }}>{dirEmoji(s.direction)}</span>
                <span style={{ fontWeight: 700 }}>{s.signal_name}</span>
                <span style={{ color: "var(--muted)", fontSize: 13 }}>{s.ts}</span>
              </div>
              <div style={{ height: 8, background: "#21262d", borderRadius: 4, overflow: "hidden" }}>
                <div
                  style={{
                    width: `${Math.min(100, Number(s.confidence) || 0)}%`,
                    height: "100%",
                    background: "var(--accent)",
                  }}
                />
              </div>
              <div style={{ fontSize: 13, marginTop: 8, color: "var(--muted)" }}>
                {s.explanation_plain_english}
              </div>
            </Card>
          ))}
        </div>

        <h2 style={{ fontSize: 18, margin: "24px 0 12px" }}>Macro indicators</h2>
        <Card>
          <div style={{ display: "grid", gap: 8 }}>
            {macro.map((m) => {
              const ch = m.pct_change;
              let arrow = "→";
              if (ch != null) arrow = ch > 0 ? "↑" : ch < 0 ? "↓" : "→";
              return (
                <div
                  key={m.metric_name}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    borderBottom: "1px solid var(--border)",
                    paddingBottom: 6,
                  }}
                >
                  <span style={{ color: "var(--muted)" }}>{m.metric_name}</span>
                  <span>
                    <b>{m.value != null ? Number(m.value).toFixed(3) : "—"}</b>{" "}
                    <span style={{ color: ch > 0 ? "var(--bull)" : ch < 0 ? "var(--bear)" : "var(--muted)" }}>
                      {arrow}
                    </span>
                  </span>
                </div>
              );
            })}
          </div>
        </Card>

        <h2 style={{ fontSize: 18, margin: "24px 0 12px" }}>Chart patterns</h2>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
          {charts.length === 0 && <div style={{ color: "var(--muted)" }}>No recent patterns.</div>}
          {charts.map((c) => (
            <Card key={`${c.id}-${c.detected_at}`} style={{ minWidth: 200, flex: "1 1 200px" }}>
              <div>
                {dirEmoji(c.direction)} <b>{c.ticker}</b> {c.pattern_name}
              </div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>{c.detected_at}</div>
            </Card>
          ))}
        </div>

        <footer style={{ marginTop: 32, fontSize: 12, color: "var(--muted)" }}>
          Auto-refresh every 60s · API {API_BASE}
        </footer>
      </div>
    </>
  );
}

import React, { useMemo, useState } from "react";

import { env } from "../lib/env.js";

const STORAGE_KEY = "geoclaw-dashboard-api-key";

function readStoredKey() {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

export function AuthGate({ children }) {
  const expectedKey = useMemo(() => env.dashboardKey, []);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [storedKey, setStoredKey] = useState(() => readStoredKey());

  const configured = Boolean(expectedKey);
  const authenticated = configured && storedKey === expectedKey;

  function handleSubmit(event) {
    event.preventDefault();
    setError("");

    if (!configured) {
      setError("REACT_APP_API_KEY is not configured for this build.");
      return;
    }

    if (input.trim() !== expectedKey) {
      setError("That API key is not valid for this dashboard.");
      return;
    }

    window.sessionStorage.setItem(STORAGE_KEY, input.trim());
    setStoredKey(input.trim());
    setInput("");
  }

  if (authenticated) {
    return children;
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="gc-panel w-full max-w-md rounded-[2rem] border border-[color:var(--gc-line)] p-8 shadow-[0_30px_90px_rgba(0,0,0,0.45)]">
        <div className="mb-3 text-xs font-semibold uppercase tracking-[0.35em] text-cyan-200/70">GeoClaw Access</div>
        <h1 className="text-3xl font-semibold text-white">Dashboard login</h1>
        <p className="mt-3 text-sm leading-6 text-[color:var(--gc-copy-soft)]">
          Enter the API key configured in <span className="font-mono text-[color:var(--gc-copy)]">REACT_APP_API_KEY</span> to unlock the live macro dashboard.
        </p>

        <form onSubmit={handleSubmit} className="mt-8 space-y-4">
          <label className="block text-xs font-semibold uppercase tracking-[0.25em] text-[color:var(--gc-copy-soft)]">
            API key
          </label>
          <input
            type="password"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Enter access key"
            className="w-full rounded-2xl border border-[color:var(--gc-line-strong)] bg-slate-950/70 px-4 py-3 font-mono text-sm text-slate-100 outline-none transition focus:border-cyan-300/60 focus:shadow-[0_0_0_4px_rgba(107,197,255,0.08)]"
          />
          {error ? <div className="rounded-2xl border border-rose-400/35 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">{error}</div> : null}
          {!configured ? (
            <div className="rounded-2xl border border-amber-300/30 bg-amber-300/10 px-4 py-3 text-sm text-amber-100">
              This build does not expose a dashboard API key yet.
            </div>
          ) : null}
          <button
            type="submit"
            className="w-full rounded-2xl bg-[linear-gradient(135deg,#8de7ff,#3fb8ff)] px-4 py-3 text-sm font-semibold uppercase tracking-[0.2em] text-slate-950 transition hover:brightness-105"
          >
            Unlock dashboard
          </button>
        </form>
      </div>
    </div>
  );
}

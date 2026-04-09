import React from "react";

function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

export function Panel({ className = "", children }) {
  return (
    <section className={cx("gc-panel rounded-3xl border border-[color:var(--gc-line)] shadow-[0_24px_80px_rgba(0,0,0,0.34)]", className)}>
      {children}
    </section>
  );
}

export function PageHeader({ eyebrow, title, subtitle, children }) {
  return (
    <div className="flex flex-col gap-5 rounded-[2rem] border border-[color:var(--gc-line)] bg-[linear-gradient(135deg,rgba(10,22,38,0.95),rgba(6,13,22,0.78))] p-6 shadow-[0_25px_80px_rgba(0,0,0,0.36)] md:flex-row md:items-end md:justify-between md:p-8">
      <div className="max-w-3xl">
        <div className="mb-3 text-xs font-semibold uppercase tracking-[0.3em] text-cyan-200/75">{eyebrow}</div>
        <h1 className="text-3xl font-semibold tracking-tight text-white md:text-5xl">{title}</h1>
        <p className="mt-4 max-w-2xl text-sm leading-6 text-[color:var(--gc-copy-soft)] md:text-base">{subtitle}</p>
      </div>
      {children ? <div className="md:text-right">{children}</div> : null}
    </div>
  );
}

export function ErrorBanner({ message }) {
  if (!message) {
    return null;
  }
  return (
    <div className="rounded-2xl border border-rose-400/35 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">
      {message}
    </div>
  );
}

export function LoadingPanel({ lines = 4 }) {
  return (
    <Panel className="p-6">
      <div className="space-y-3">
        {Array.from({ length: lines }).map((_, index) => (
          <div
            key={index}
            className="h-4 animate-pulse rounded-full bg-white/8"
            style={{ width: `${92 - index * 12}%` }}
          />
        ))}
      </div>
    </Panel>
  );
}

export function EmptyState({ title, body }) {
  return (
    <Panel className="p-6 text-center">
      <div className="text-sm font-semibold uppercase tracking-[0.3em] text-[color:var(--gc-copy-soft)]">{title}</div>
      <div className="mt-4 text-sm text-[color:var(--gc-copy-soft)]">{body}</div>
    </Panel>
  );
}

export function SignalDirectionBadge({ direction }) {
  const clean = String(direction || "HOLD").toUpperCase();
  const tone =
    clean === "BUY"
      ? "border-emerald-400/35 bg-emerald-400/12 text-emerald-200"
      : clean === "SELL"
        ? "border-rose-400/35 bg-rose-400/12 text-rose-200"
        : "border-amber-300/35 bg-amber-300/12 text-amber-100";
  return (
    <span className={cx("inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-semibold tracking-[0.18em]", tone)}>
      {clean}
    </span>
  );
}

export function ConfidenceBar({ value }) {
  const cleanValue = Math.max(0, Math.min(100, Number(value) || 0));
  const tone =
    cleanValue >= 70 ? "from-cyan-300 via-cyan-400 to-emerald-300" : cleanValue >= 45 ? "from-amber-300 via-amber-400 to-orange-300" : "from-rose-400 via-rose-500 to-orange-400";
  return (
    <div className="w-full">
      <div className="mb-2 flex items-center justify-between text-[11px] uppercase tracking-[0.18em] text-[color:var(--gc-copy-soft)]">
        <span>Confidence</span>
        <span className="font-mono text-[color:var(--gc-copy)]">{cleanValue.toFixed(0)}%</span>
      </div>
      <div className="h-2.5 overflow-hidden rounded-full bg-white/8">
        <div className={cx("h-full rounded-full bg-gradient-to-r", tone)} style={{ width: `${cleanValue}%` }} />
      </div>
    </div>
  );
}

export function PriceSparkline({ values = [], direction = "flat" }) {
  const cleanValues = values.filter((value) => Number.isFinite(value));
  if (!cleanValues.length) {
    return <div className="h-16 rounded-2xl border border-dashed border-[color:var(--gc-line)] bg-white/4" />;
  }
  const min = Math.min(...cleanValues);
  const max = Math.max(...cleanValues);
  const range = max - min || 1;
  const points = cleanValues
    .map((value, index) => {
      const x = (index / Math.max(cleanValues.length - 1, 1)) * 100;
      const y = 100 - ((value - min) / range) * 100;
      return `${x},${y}`;
    })
    .join(" ");
  const stroke = direction === "up" ? "#39d98a" : direction === "down" ? "#ff6b6b" : "#f5b04c";

  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-16 w-full overflow-visible">
      <polyline
        fill="none"
        stroke={stroke}
        strokeWidth="5"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={points}
      />
    </svg>
  );
}

export function formatTimestamp(value) {
  if (!value) {
    return "Unavailable";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Unavailable";
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatPrice(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "N/A";
  }
  const decimals = numeric >= 1000 ? 2 : numeric >= 10 ? 3 : 4;
  return new Intl.NumberFormat("en-GB", {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  }).format(numeric);
}

import React, { useCallback, useMemo, useState } from "react";

import { usePollingResource } from "../hooks/usePollingResource.js";
import { api } from "../lib/api.js";
import {
  EmptyState,
  ErrorBanner,
  LoadingPanel,
  PageHeader,
  Panel,
  SignalDirectionBadge,
  formatTimestamp,
} from "../components/ui.jsx";

export function SignalsPage() {
  const [directionFilter, setDirectionFilter] = useState("ALL");
  const [sortBy, setSortBy] = useState("ts");
  const [sortDirection, setSortDirection] = useState("desc");
  const loadSignals = useCallback(() => api.getSignalsHistory({ hours: 720, limit: 2000 }), []);
  const { data, loading, error } = usePollingResource(loadSignals, [loadSignals], 60000);

  const rows = useMemo(() => {
    const list = [...(data?.signals || [])];
    const filtered = directionFilter === "ALL" ? list : list.filter((item) => String(item.direction || "").toUpperCase() === directionFilter);
    filtered.sort((left, right) => {
      const leftValue = sortBy === "confidence" ? Number(left.confidence || 0) : new Date(left.ts || 0).getTime();
      const rightValue = sortBy === "confidence" ? Number(right.confidence || 0) : new Date(right.ts || 0).getTime();
      if (sortDirection === "asc") {
        return leftValue - rightValue;
      }
      return rightValue - leftValue;
    });
    return filtered;
  }, [data?.signals, directionFilter, sortBy, sortDirection]);

  function toggleSort(nextSortBy) {
    if (sortBy === nextSortBy) {
      setSortDirection((current) => (current === "desc" ? "asc" : "desc"));
      return;
    }
    setSortBy(nextSortBy);
    setSortDirection(nextSortBy === "confidence" ? "desc" : "desc");
  }

  if (loading && !data) {
    return <LoadingPanel lines={6} />;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Signal Ledger"
        title="Full signal history"
        subtitle="Filter by direction, sort by timestamp or confidence, and inspect how each signal lands in the shared asset-class taxonomy."
      >
        <div className="rounded-3xl border border-[color:var(--gc-line)] bg-white/4 px-5 py-4">
          <div className="text-xs font-semibold uppercase tracking-[0.28em] text-[color:var(--gc-copy-soft)]">Rows loaded</div>
          <div className="mt-2 font-mono text-lg text-white">{rows.length}</div>
        </div>
      </PageHeader>

      <ErrorBanner message={error} />

      <Panel className="p-6">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="flex flex-wrap gap-2">
            {["ALL", "BUY", "SELL", "HOLD"].map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setDirectionFilter(value)}
                className={`rounded-full px-4 py-2 text-xs font-semibold uppercase tracking-[0.24em] transition ${
                  directionFilter === value
                    ? "bg-cyan-300/12 text-cyan-100 shadow-[inset_0_0_0_1px_rgba(107,197,255,0.32)]"
                    : "border border-[color:var(--gc-line)] text-[color:var(--gc-copy-soft)] hover:text-white"
                }`}
              >
                {value}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => toggleSort("ts")}
              className="rounded-full border border-[color:var(--gc-line)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.24em] text-[color:var(--gc-copy-soft)] transition hover:text-white"
            >
              Sort date {sortBy === "ts" ? `(${sortDirection})` : ""}
            </button>
            <button
              type="button"
              onClick={() => toggleSort("confidence")}
              className="rounded-full border border-[color:var(--gc-line)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.24em] text-[color:var(--gc-copy-soft)] transition hover:text-white"
            >
              Sort confidence {sortBy === "confidence" ? `(${sortDirection})` : ""}
            </button>
          </div>
        </div>
      </Panel>

      {!rows.length ? (
        <EmptyState title="No signals" body="No signal rows matched the current filter." />
      ) : (
        <Panel className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full border-collapse">
              <thead>
                <tr className="border-b border-[color:var(--gc-line)] bg-white/3 text-left text-[11px] font-semibold uppercase tracking-[0.24em] text-[color:var(--gc-copy-soft)]">
                  <th className="px-4 py-4">Timestamp</th>
                  <th className="px-4 py-4">Signal name</th>
                  <th className="px-4 py-4">Direction</th>
                  <th className="px-4 py-4">Confidence</th>
                  <th className="px-4 py-4">Asset class</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((signal) => (
                  <tr key={`${signal.id}-${signal.ts}`} className="border-b border-[color:var(--gc-line)]/80 text-sm">
                    <td className="px-4 py-4 font-mono text-[color:var(--gc-copy-soft)]">{formatTimestamp(signal.ts)}</td>
                    <td className="px-4 py-4 text-white">{signal.signal_name}</td>
                    <td className="px-4 py-4">
                      <SignalDirectionBadge direction={signal.direction} />
                    </td>
                    <td className="px-4 py-4 font-mono text-white">{Number(signal.confidence || 0).toFixed(1)}%</td>
                    <td className="px-4 py-4 text-[color:var(--gc-copy-soft)]">{signal.asset_class}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  );
}

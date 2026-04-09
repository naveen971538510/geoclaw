import React, { useCallback } from "react";

import { usePollingResource } from "../hooks/usePollingResource.js";
import { api } from "../lib/api.js";
import {
  ConfidenceBar,
  EmptyState,
  ErrorBanner,
  LoadingPanel,
  PageHeader,
  Panel,
  SignalDirectionBadge,
  formatTimestamp,
} from "../components/ui.jsx";

function biasTone(label) {
  if (label === "BULLISH") {
    return "text-emerald-200";
  }
  if (label === "BEARISH") {
    return "text-rose-200";
  }
  return "text-amber-100";
}

export function DashboardPage() {
  const loadOverview = useCallback(() => api.getDashboardOverview(), []);
  const { data, loading, error } = usePollingResource(loadOverview, [loadOverview], 60000);

  if (loading && !data) {
    return (
      <div className="space-y-6">
        <LoadingPanel lines={4} />
        <LoadingPanel lines={5} />
      </div>
    );
  }

  const bias = data?.market_bias || {};
  const groups = data?.group_order || [];

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Live Macro Board"
        title="Morning bias, active signals, and live rate pulse"
        subtitle="This surface rolls forward every minute so you can see the current macro stack without opening the deeper research tools."
      >
        <div className="rounded-3xl border border-[color:var(--gc-line)] bg-white/4 px-5 py-4">
          <div className="text-xs font-semibold uppercase tracking-[0.28em] text-[color:var(--gc-copy-soft)]">Last updated</div>
          <div className="mt-2 font-mono text-lg text-white">{formatTimestamp(data?.last_updated)}</div>
        </div>
      </PageHeader>

      <ErrorBanner message={error} />

      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Panel className="p-6 md:p-8">
          <div className="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.3em] text-[color:var(--gc-copy-soft)]">Market bias</div>
              <div className={`mt-3 text-4xl font-semibold tracking-tight ${biasTone(bias.label)}`}>{bias.label || "NEUTRAL"}</div>
            </div>
            <div className="rounded-3xl border border-[color:var(--gc-line)] bg-white/5 px-5 py-4">
              <div className="text-xs font-semibold uppercase tracking-[0.25em] text-[color:var(--gc-copy-soft)]">Weighted confidence</div>
              <div className="mt-2 font-mono text-3xl text-white">{Number(bias.weighted_confidence || 0).toFixed(1)}%</div>
            </div>
          </div>
          <div className="mt-8 grid gap-4 md:grid-cols-3">
            {[
              { label: "BUY weight", value: bias.buy_weight, tone: "from-emerald-300 to-cyan-300" },
              { label: "SELL weight", value: bias.sell_weight, tone: "from-rose-300 to-orange-300" },
              { label: "HOLD weight", value: bias.hold_weight, tone: "from-amber-300 to-yellow-200" },
            ].map((item) => (
              <div key={item.label} className="rounded-3xl border border-[color:var(--gc-line)] bg-white/4 p-4">
                <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-[color:var(--gc-copy-soft)]">{item.label}</div>
                <div className="mt-3 font-mono text-2xl text-white">{Number(item.value || 0).toFixed(1)}</div>
                <div className="mt-4 h-2 overflow-hidden rounded-full bg-white/8">
                  <div
                    className={`h-full rounded-full bg-gradient-to-r ${item.tone}`}
                    style={{ width: `${Math.min(100, Number(item.value || 0))}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </Panel>

        <Panel className="p-6 md:p-8">
          <div className="text-xs font-semibold uppercase tracking-[0.3em] text-[color:var(--gc-copy-soft)]">Operator note</div>
          <p className="mt-4 text-sm leading-7 text-[color:var(--gc-copy-soft)]">
            The bias card weights the latest signal state by confidence, so mixed BUY and SELL calls compress toward neutral while concentrated conviction expands the score.
          </p>
          <div className="mt-6 rounded-3xl border border-[color:var(--gc-line)] bg-white/4 p-5">
            <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-[color:var(--gc-copy-soft)]">Polling cadence</div>
            <div className="mt-2 font-mono text-xl text-white">60s live refresh</div>
            <div className="mt-4 text-sm leading-6 text-[color:var(--gc-copy-soft)]">
              Grouping follows the shared taxonomy used by the morning briefing: Rates &amp; Inflation, Labour &amp; Growth, then Macro/Other.
            </div>
          </div>
        </Panel>
      </div>

      {!data?.signals?.length ? (
        <EmptyState title="No active signals" body="Signal state has not been populated yet, or the backend returned an empty cycle." />
      ) : (
        <div className="grid gap-6">
          {groups.map((group) => {
            const items = data?.grouped_signals?.[group] || [];
            return (
              <Panel key={group} className="p-6 md:p-8">
                <div className="mb-6 flex items-center justify-between gap-4">
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.28em] text-[color:var(--gc-copy-soft)]">Signal bucket</div>
                    <h2 className="mt-2 text-2xl font-semibold text-white">{group}</h2>
                  </div>
                  <div className="rounded-full border border-[color:var(--gc-line)] px-4 py-2 font-mono text-sm text-[color:var(--gc-copy-soft)]">
                    {items.length} active
                  </div>
                </div>
                <div className="space-y-4">
                  {items.length ? (
                    items.map((signal) => (
                      <div
                        key={`${signal.signal_name}-${signal.direction}`}
                        className="rounded-[1.75rem] border border-[color:var(--gc-line)] bg-[color:var(--gc-panel-strong)] p-5"
                      >
                        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                          <div className="max-w-3xl">
                            <div className="flex flex-wrap items-center gap-3">
                              <h3 className="text-lg font-semibold text-white">{signal.signal_name}</h3>
                              <SignalDirectionBadge direction={signal.direction} />
                            </div>
                            <p className="mt-3 text-sm leading-6 text-[color:var(--gc-copy-soft)]">
                              {signal.explanation_plain_english || "No explanation provided for this signal yet."}
                            </p>
                          </div>
                          <div className="w-full max-w-xs shrink-0">
                            <ConfidenceBar value={signal.confidence} />
                          </div>
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="rounded-[1.75rem] border border-dashed border-[color:var(--gc-line)] bg-white/3 px-5 py-8 text-sm text-[color:var(--gc-copy-soft)]">
                      No active signals in this group right now.
                    </div>
                  )}
                </div>
              </Panel>
            );
          })}
        </div>
      )}
    </div>
  );
}

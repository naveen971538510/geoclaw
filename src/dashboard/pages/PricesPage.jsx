import React, { useCallback } from "react";

import { usePollingResource } from "../hooks/usePollingResource.js";
import { api } from "../lib/api.js";
import {
  ErrorBanner,
  LoadingPanel,
  PageHeader,
  Panel,
  PriceSparkline,
  formatPrice,
  formatTimestamp,
} from "../components/ui.jsx";

function directionGlyph(direction) {
  if (direction === "up") {
    return "↑";
  }
  if (direction === "down") {
    return "↓";
  }
  return "→";
}

function directionTone(direction) {
  if (direction === "up") {
    return "text-emerald-200";
  }
  if (direction === "down") {
    return "text-rose-200";
  }
  return "text-amber-100";
}

export function PricesPage() {
  const loadPrices = useCallback(() => api.getPricesPanel(), []);
  const { data, loading, error } = usePollingResource(loadPrices, [loadPrices], 60000);

  if (loading && !data) {
    return <LoadingPanel lines={5} />;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Cross-Asset Tape"
        title="BTC, SPX, and gold on one live panel"
        subtitle="Prices are pulled from the backend price store and refreshed every minute so directional context stays aligned with the macro signal engine."
      >
        <div className="rounded-3xl border border-[color:var(--gc-line)] bg-white/4 px-5 py-4">
          <div className="text-xs font-semibold uppercase tracking-[0.28em] text-[color:var(--gc-copy-soft)]">Feed timestamp</div>
          <div className="mt-2 font-mono text-lg text-white">{formatTimestamp(data?.captured_at)}</div>
        </div>
      </PageHeader>

      <ErrorBanner message={error} />

      <div className="grid gap-6 lg:grid-cols-3">
        {(data?.prices || []).map((price) => (
          <Panel key={price.symbol} className="p-6">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.3em] text-[color:var(--gc-copy-soft)]">{price.label}</div>
                <h2 className="mt-2 text-2xl font-semibold text-white">{price.name}</h2>
              </div>
              <div className={`font-mono text-3xl ${directionTone(price.direction)}`}>{directionGlyph(price.direction)}</div>
            </div>
            <div className="mt-8 font-mono text-4xl text-white">{formatPrice(price.price)}</div>
            <div className="mt-2 text-xs uppercase tracking-[0.22em] text-[color:var(--gc-copy-soft)]">Last fetch {formatTimestamp(price.last_fetch_time)}</div>
            <div className="mt-6 rounded-[1.75rem] border border-[color:var(--gc-line)] bg-black/20 p-4">
              <PriceSparkline values={price.sparkline} direction={price.direction} />
            </div>
          </Panel>
        ))}
      </div>
    </div>
  );
}

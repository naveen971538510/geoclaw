import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { loadStripe } from "@stripe/stripe-js";

import { api } from "../lib/api.js";
import { env } from "../lib/env.js";
import { ErrorBanner, PageHeader, Panel } from "../components/ui.jsx";

const TIERS = [
  { key: "basic", name: "Basic", price: "£29/mo", summary: "Single-operator access to the live dashboard and signal history." },
  { key: "pro", name: "Pro", price: "£99/mo", summary: "Adds broader monitoring coverage and a faster daily operating loop." },
  { key: "institutional", name: "Institutional", price: "£499/mo", summary: "For teams that need the full operating surface and premium support." },
];

export function SubscribePage() {
  const [pendingTier, setPendingTier] = useState("");
  const [error, setError] = useState("");
  const [stripeReady, setStripeReady] = useState(false);
  const [searchParams] = useSearchParams();

  const status = useMemo(() => searchParams.get("status") || "", [searchParams]);

  useEffect(() => {
    let cancelled = false;
    async function warmStripe() {
      if (!env.stripeKey) {
        setStripeReady(false);
        return;
      }
      try {
        const stripe = await loadStripe(env.stripeKey);
        if (!cancelled) {
          setStripeReady(Boolean(stripe));
        }
      } catch {
        if (!cancelled) {
          setStripeReady(false);
        }
      }
    }
    warmStripe();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleCheckout(tier) {
    setPendingTier(tier);
    setError("");
    try {
      const payload = await api.createCheckoutSession(tier);
      if (!payload.checkout_url) {
        throw new Error("Stripe Checkout did not return a redirect URL.");
      }
      window.location.assign(payload.checkout_url);
    } catch (err) {
      setError(String(err?.message || err));
      setPendingTier("");
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Subscription"
        title="Choose the GeoClaw operating tier"
        subtitle="Stripe Checkout handles the hosted payment flow. Pricing is monthly, denominated in GBP, and tuned for operator, team, and institutional usage."
      >
        <div className="rounded-3xl border border-[color:var(--gc-line)] bg-white/4 px-5 py-4">
          <div className="text-xs font-semibold uppercase tracking-[0.28em] text-[color:var(--gc-copy-soft)]">Stripe status</div>
          <div className="mt-2 font-mono text-lg text-white">{stripeReady ? "ready" : "not ready"}</div>
        </div>
      </PageHeader>

      {status === "success" ? (
        <div className="rounded-2xl border border-emerald-400/35 bg-emerald-400/10 px-4 py-3 text-sm text-emerald-100">
          Checkout completed. Stripe redirected back successfully.
        </div>
      ) : null}
      {status === "cancelled" ? (
        <div className="rounded-2xl border border-amber-300/35 bg-amber-300/10 px-4 py-3 text-sm text-amber-100">
          Checkout was cancelled before payment confirmation.
        </div>
      ) : null}

      <ErrorBanner message={error || (!env.stripeKey ? "REACT_APP_STRIPE_KEY is not configured for this frontend build." : "")} />

      <div className="grid gap-6 xl:grid-cols-3">
        {TIERS.map((tier) => (
          <Panel key={tier.key} className="flex h-full flex-col p-6">
            <div className="text-xs font-semibold uppercase tracking-[0.3em] text-[color:var(--gc-copy-soft)]">{tier.name}</div>
            <div className="mt-4 text-4xl font-semibold text-white">{tier.price}</div>
            <p className="mt-4 flex-1 text-sm leading-7 text-[color:var(--gc-copy-soft)]">{tier.summary}</p>
            <button
              type="button"
              onClick={() => handleCheckout(tier.key)}
              disabled={!env.stripeKey || pendingTier === tier.key}
              className="mt-8 rounded-2xl bg-[linear-gradient(135deg,#8de7ff,#3fb8ff)] px-4 py-3 text-sm font-semibold uppercase tracking-[0.2em] text-slate-950 transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {pendingTier === tier.key ? "Launching checkout" : `Subscribe ${tier.name}`}
            </button>
          </Panel>
        ))}
      </div>
    </div>
  );
}

import { env } from "./env.js";

function buildQuery(params = {}) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") {
      return;
    }
    search.set(key, String(value));
  });
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

async function request(path, options = {}) {
  const response = await fetch(`${env.apiBase}${path}`, {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }

  if (!response.ok || payload.status === "error") {
    throw new Error(String(payload.error || `${response.status} ${response.statusText}`));
  }

  return payload;
}

export const api = {
  getDashboardOverview() {
    return request("/dashboard/overview");
  },
  getPricesPanel() {
    return request("/prices" + buildQuery({ symbols: "BTCUSD,SPX,XAUUSD", points: 18 }));
  },
  getSignalsHistory({ hours = 720, limit = 1500, direction = "" } = {}) {
    return request("/signals" + buildQuery({ hours, limit, direction }));
  },
  createCheckoutSession(tier) {
    return request("/checkout/create-session", {
      method: "POST",
      body: JSON.stringify({ tier }),
    });
  },
};

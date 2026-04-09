const trimTrailingSlash = (value) => String(value || "").replace(/\/+$/, "");

export const env = {
  apiBase: trimTrailingSlash(import.meta.env.REACT_APP_API_BASE || import.meta.env.VITE_API_BASE || "/api"),
  dashboardKey: String(import.meta.env.REACT_APP_API_KEY || import.meta.env.VITE_API_KEY || "").trim(),
  stripeKey: String(import.meta.env.REACT_APP_STRIPE_KEY || import.meta.env.VITE_STRIPE_KEY || "").trim(),
};

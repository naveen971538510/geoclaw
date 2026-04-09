import React from "react";
import { NavLink } from "react-router-dom";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/prices", label: "Prices" },
  { to: "/signals", label: "Signals" },
  { to: "/subscribe", label: "Subscribe" },
];

function navClassName({ isActive }) {
  return [
    "rounded-full px-4 py-2 text-sm font-semibold tracking-[0.14em] transition",
    isActive
      ? "bg-cyan-300/12 text-cyan-100 shadow-[inset_0_0_0_1px_rgba(107,197,255,0.28)]"
      : "text-[color:var(--gc-copy-soft)] hover:bg-white/6 hover:text-white",
  ].join(" ");
}

function signOut() {
  window.sessionStorage.removeItem("geoclaw-dashboard-api-key");
  window.location.reload();
}

export function AppShell({ children }) {
  return (
    <div className="min-h-screen px-4 py-4 sm:px-6 lg:px-10">
      <div className="mx-auto max-w-7xl">
        <header className="gc-panel sticky top-4 z-30 mb-6 rounded-[1.75rem] border border-[color:var(--gc-line)] px-5 py-4 shadow-[0_20px_70px_rgba(0,0,0,0.32)] backdrop-blur-xl">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.38em] text-cyan-200/75">GeoClaw</div>
              <div className="mt-1 text-sm text-[color:var(--gc-copy-soft)]">Macro signal surface for live monitoring and operator review</div>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <nav className="flex flex-wrap gap-2">
                {NAV_ITEMS.map((item) => (
                  <NavLink key={item.to} to={item.to} className={navClassName}>
                    {item.label}
                  </NavLink>
                ))}
              </nav>
              <button
                type="button"
                onClick={signOut}
                className="rounded-full border border-[color:var(--gc-line)] px-4 py-2 text-xs font-semibold uppercase tracking-[0.24em] text-[color:var(--gc-copy-soft)] transition hover:border-cyan-300/30 hover:text-white"
              >
                Sign out
              </button>
            </div>
          </div>
        </header>
        <main>{children}</main>
      </div>
    </div>
  );
}

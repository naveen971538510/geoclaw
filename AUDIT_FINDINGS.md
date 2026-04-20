# GeoClaw Pre-Launch Audit — Findings

Date: 2026-04-20
Scope: full read of auth, middleware, UI, DB, deployment.
Status legend: ✅ fixed in this branch · ⚠️ deferred (documented) · 🔴 open

---

## ✅ Fixed

### Auth / middleware
1. **Localhost auth bypass on by default** — any peer connecting to `127.0.0.1` (inside the container network, a sidecar, etc.) was treated as authenticated. Now opt-in via `GEOCLAW_ALLOW_LOCALHOST_AUTH=1`; prod defaults to deny. (`middleware/auth.py`)
2. **X-Forwarded-For spoofing bypasses rate limit** — untrusted peers could rotate `X-Forwarded-For` to mint fresh buckets. Now only honoured when the immediate peer is in `GEOCLAW_TRUSTED_PROXIES`. (`middleware/rate_limit.py`)
3. **`?token=` accepted on every /api endpoint** — a JWT leaked via Referer or logs could be replayed on any API path. Now restricted to `QUERY_TOKEN_PATHS` (verify-email, reset-password, /api/stream SSE). (`middleware/auth.py`)
4. **JWT `sub` not type-checked** — `sub=0`, `sub=true`, or `sub="admin"` would pass. Now requires `isinstance(sub, int) and not isinstance(sub, bool) and sub > 0`. (`services/auth_service.py`, `middleware/auth.py`)
5. **Prefix match on public paths** — `/api/auth/signup-admin` was public because it started with `/api/auth/signup`. Added trailing-slash boundary matching. (`middleware/auth.py`)

### Auth service
6. **Long-password DoS** — unbounded input to PBKDF2 (700k iters). Capped at `MAX_PASSWORD_LEN=1024`; `verify_password` short-circuits on oversized input. (`services/auth_service.py`)
7. **Email CRLF injection** — `is_valid_email` accepted `\r\n`, enabling header injection downstream. Now rejected; `normalize_email` also caps at `MAX_EMAIL_LEN=254`. (`services/auth_service.py`)
8. **Case-insensitive email uniqueness not enforced** — two users could register as `Foo@x.com` and `foo@x.com`. Replaced non-unique index with `UNIQUE INDEX ON (lower(email))`. (`intelligence/db.py`)

### Token flow
9. **Token consumption race** — `SELECT FOR UPDATE` pattern relied on implicit autocommit semantics that aren't guaranteed. Replaced with atomic `UPDATE … WHERE consumed_at IS NULL RETURNING`. (`services/user_repository.py`)
10. **Password change did not invalidate outstanding reset tokens** — a stolen reset link remained usable after the user changed their password. `update_password` now consumes all outstanding reset tokens for the user. (`services/user_repository.py`)

### Email
11. **Header injection via From address/name** — concatenated string formatting. Now uses `email.utils.formataddr`; strips non-printable and CR/LF. (`services/email_service.py`)
12. **Subject/body CRLF injection** — `_safe_subject` replaces CR/LF with space, caps at 300 chars; body capped at 200k chars. (`services/email_service.py`)
13. **Plaintext SMTP on by default** — now refuses to send unless TLS/SSL is enabled or `SMTP_ALLOW_PLAINTEXT=1` explicitly set. (`services/email_service.py`)

### UI / SPA shim
14. **Tokens leaked via Referer** — `<meta name="referrer" content="no-referrer">` on login/forgot/reset/verify pages.
15. **Tokens visible in URL after consume** — `history.replaceState` strips `?token=` immediately after the page reads it. (`ui/reset_password.html`, `ui/verify_email.html`)
16. **Auth shim redirect loop** when `localStorage` is blocked (Safari private mode, browser policy). Now detects failure and surfaces an explicit error instead of bouncing to /login. (`ui/login.html`)
17. **EventSource `?token=` appended to every stream URL** — could leak to third-party analytics. Now scoped to `/api/stream` only via `SSE_PATHS` regex. (`services/terminal_ui_service.py`)
18. **Auth pages 401-loop** — `/forgot-password`, `/reset-password`, `/verify-email` were gated and bounced to `/login`. Added to PUBLIC_PATHS allowlist. (`services/terminal_ui_service.py`)

### Deployment
19. **Legacy `main.py` exposed publicly on :8001** — lacks tenant scoping across endpoints, so cross-tenant reads/writes were possible. Removed from `Dockerfile` CMD and from `fly.toml` services. If needed for cron, run bound to 127.0.0.1. (`Dockerfile`, `fly.toml`)

---

## ⚠️ Deferred (documented, not blockers)

### A. `main.py` has unscoped endpoints
Whole-file rewrite needed. Mitigation: not exposed in prod (fix 19). Do not re-expose until every endpoint calls `scope_where(user_id)` or is explicitly public.

### B. Session revocation on password change
Outstanding JWTs remain valid until their TTL expires. Fix needs a `password_changed_at` column + claim check in `verify_access_token`. Current TTL is short; acceptable for launch.
**How to apply later:** add column, include in JWT as `pwc`, reject tokens where `pwc < users.password_changed_at`.

### C. `POST /api/agent/run` does not propagate `user_id`
Jobs run under the anonymous scope. Users see shared results only. Not a security issue — but multi-tenant personalization on agent jobs is broken.

### D. `POST /api/checkout/create-session` doesn't reconcile Stripe customer ↔ user
Billing records are anonymous. Needs Stripe customer lookup-or-create keyed by `users.id`.

### E. `migration.py` dialect drift
SQL uses Postgres-only constructs (`NOW()`, `RETURNING`) that won't run against the SQLite dev backend. Dev uses a separate bootstrap path, so not felt in practice, but the unified migration story is broken.

### F. Signup enumeration
`/api/auth/signup` returns `409 email_taken` for existing addresses, exposing which emails are registered. Request-reset is anti-enumerated (always 200); signup is not. Accepted tradeoff for UX.

### G. Rate limit is per-IP, not per-user
A logged-in user behind CGNAT shares a bucket with strangers. Acceptable at current scale.

---

## 🔴 Open — none

Everything discovered in this audit is either fixed or listed above as deferred with a documented mitigation.

---

## Test coverage added

`tests/test_auth_and_tenant.py` — 30 tests total, all green:

- Password length caps (hash + verify)
- Email CRLF rejection + length cap
- JWT non-positive/bool `sub` rejection
- Public-path boundary (signup-admin ≠ signup)
- Localhost auth opt-out
- Legacy-token match required when locked down
- Query-token scoped to reset/verify paths only
- XFF rotation does not bypass rate limit

# GeoClaw

GeoClaw is a self-hostable stock intelligence terminal. A FastAPI backend ingests macro, price, news, and chart signals, scores them, and serves them through a terminal-style web UI plus an optional Telegram bot.

> **Status:** early, single-tenant → multi-tenant transition. Do **not** treat output as financial advice — see the in-app disclaimer banner.

## Stack

- **API / web:** FastAPI (`dashboard_api.py`, port `8001` by default).
- **Bot:** `telegram_bot.py` (optional).
- **Database:** PostgreSQL in production via `DATABASE_URL`; SQLite fallback for local dev only.
- **Auth:** JWT (HS256) signed with `GEOCLAW_JWT_SECRET`, with a legacy `GEOCLAW_LOCAL_TOKEN` fallback for scheduled jobs.
- **Rate limits:** in-memory per-IP sliding window — 60 req/60s general, 10 req/60s for LLM-backed endpoints (see `middleware/rate_limit.py`).

## Quick start (local dev)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Minimum viable env
export GEOCLAW_JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"
export GROQ_API_KEY='...'     # or OPENAI_API_KEY / GEMINI_API_KEY

python3 dashboard_api.py
```

Open `http://127.0.0.1:8001/` — it redirects to `/dashboard`. Localhost requests bypass auth in dev.

## Sign up / log in

All multi-user deployments go through the web login flow:

1. Visit `/login` — tabbed signup / sign-in page.
2. Signup posts to `POST /api/auth/signup` with `{email, password, display_name?}`; login posts to `POST /api/auth/login`.
3. On success the page stores the JWT in `localStorage.gc_access_token` and the user profile in `localStorage.gc_user`, then redirects to `/dashboard`.
4. Front-end code sends the token as `Authorization: Bearer <jwt>` on every `/api/*` call. `GET /api/auth/me` returns the current user.

Tokens are valid for 7 days. Passwords are hashed with PBKDF2-HMAC-SHA256 (700k iterations, per-user salt) — stored as `pbkdf2_sha256$iter$salt$hash`.

Data is scoped per user via a nullable `user_id` column on every user-visible table. Rows with `user_id IS NULL` are shared/system data; authenticated users see shared rows plus their own. See `services/tenant_scope.py`.

## Production deployment checklist

Full variable reference lives in [ENV_VARS.md](ENV_VARS.md). The minimum for a public launch:

```bash
export GEOCLAW_ENV=production
export DATABASE_URL='postgres://user:pass@host:5432/geoclaw'
export GEOCLAW_JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"
export GEOCLAW_PRODUCTION_ORIGIN='https://your-public-domain.example'
export GROQ_API_KEY='...'
```

When `GEOCLAW_ENV=production` the app refuses to start without `DATABASE_URL` and rejects `GEOCLAW_DB_BACKEND=sqlite` — SQLite cannot handle concurrent writes from multiple users.

Railway / Fly / Heroku-style hosts use the `Procfile`:

```
api: python3 dashboard_api.py
bot: python3 telegram_bot.py
```

## Tests

```bash
python -m pytest tests/test_auth_and_tenant.py -v
# or, without pytest:
python3 tests/test_auth_and_tenant.py
```

Covers password hashing, JWT sign/verify/expiry, tenant row-scoping, and the auth + rate-limit middleware.

## Key files

- `dashboard_api.py` — FastAPI app, routes, SPA entry.
- `services/auth_service.py` — password hashing + JWT (stdlib only, no PyJWT).
- `services/user_repository.py` — signup / authenticate.
- `services/tenant_scope.py` — per-user row-scoping helpers.
- `middleware/auth.py`, `middleware/rate_limit.py` — request-time gates.
- `intelligence/db.py` — schema, including `users` / `user_usage` tables and `user_id` columns.
- `ui/login.html` — signup / sign-in page.
- `ui/terminal.html` — terminal SPA.

## Disclaimer

GeoClaw is a research and observability tool. It surfaces signals and aggregated news; it does not provide financial, investment, tax, or legal advice. A dismissible banner is injected on every rendered page — do not remove it before deployment.

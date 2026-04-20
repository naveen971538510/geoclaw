# GeoClaw Public Launch Handover

Complete reference for running, deploying, and maintaining GeoClaw as a multi-tenant SaaS.

## What is GeoClaw?

Self-hostable stock intelligence terminal. FastAPI + Postgres backend; React SPA frontend. Multi-tenant (row-level scoping by `user_id`). Educational market research tool — signals, theses, backtests, live Nikkei 225 pricing.

## Current State (as of commit 1e4622b)

✅ **Landed for public launch:**
- Row-level tenancy applied across all `/api/*` reads
- Email verification + password reset flow (verify-email, forgot-password, reset-password)
- SPA auth shim (localStorage JWT → Authorization header on `/api/*` calls)
- 20/20 auth + tenant tests passing
- Four unified auth pages (login, forgot-password, reset-password, verify-email) with cohesive dark design
- Vendored all 18 service/intelligence modules so a fresh clone boots
- ENV_VARS documented for SMTP, JWT, Postgres, and deployment

❌ **Not yet ready:**
- No UI tests (design verified visually in preview; no automation)
- No production monitoring/alerting setup
- No load-testing of multi-tenant row scoping
- Email templates are plain text (could be HTML)

---

## Architecture Overview

### Authentication & Tenancy

**How it works:**
1. User signs up at `/login` → POST `/api/auth/signup` → JWT issued
2. Token stored in `localStorage.gc_access_token`
3. SPA shim (`services/terminal_ui_service.py`) wraps `fetch()` and `EventSource`:
   - Same-origin `/api/*` calls: injects `Authorization: Bearer <token>` header
   - 401 response: clears token, redirects to `/login`
4. Backend middleware (`middleware/auth.py`) verifies JWT, sets `request.state.user_id`
5. All `/api/*` handlers call `current_user_id(request)` and scope reads via `scope_where(uid)`

**Row-level scoping pattern:**
```python
# Get current user
uid = current_user_id(request)

# Scope query (shared rows have NULL user_id; private rows have user_id = uid)
scope_clause, scope_params = scope_where(uid)

# Apply to SQL
query = f"SELECT * FROM signals WHERE {scope_clause}"
rows = conn.execute(query, scope_params)
```

**Special cases:**
- `/api/auth/*` endpoints are public (no scoping)
- `/api/stream` captures `user_id` at SSE connect time (middleware doesn't re-run on each event)
- Endpoints using `services.db_helpers.query()` (SQLite-native `?` placeholders) pass `placeholder="?"` to `scope_where()`

### Database

**Postgres in prod; SQLite in dev.**

Schema:
- `users` — email, password_hash, display_name, role, is_active, email_verified_at, created_at, last_login_at
- `auth_tokens` — user_id (FK CASCADE), kind (verify_email / password_reset), token_hash (unique), expires_at, consumed_at, created_at
- All data rows have nullable `user_id` column:
  - `NULL` = shared/system data (visible to all users)
  - `user_id = X` = private to user X

**In-place migration for existing DBs:**
```python
# See intelligence/db.py __init__()
# ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ
# CREATE TABLE IF NOT EXISTS auth_tokens (...)
```

### Email

Opt-in via `SMTP_HOST` environment variable.

**Flow:**
1. User signs up → `issue_token(user_id, kind='verify_email')` → plaintext token returned
2. Token emailed: `https://<your-domain>/verify-email?token=<plaintext>`
3. User clicks link → browser auto-POSTs to `/api/auth/verify-email` with token
4. Backend: `consume_token(token, kind='verify_email')` — validates, checks expiry, marks `email_verified_at`

**Same for password reset:**
- Request: `/api/auth/request-password-reset` (email only, always 200 to avoid enumeration)
- Receive email with: `https://<your-domain>/reset-password?token=<plaintext>`
- Reset: `/api/auth/reset-password` with token + new_password

**Unconfigured behavior:**
If `SMTP_HOST` unset, `email_service.send_email()` logs to stdout. Useful for local dev.

### Frontend SPA

React app (compiled to `static/dashboard-app/assets/index-*.js`).

**Auth shim** (`services/terminal_ui_service.py`):
- Injected into every HTML page before `</head>`
- Wraps `window.fetch`: injects `Authorization: Bearer` header for same-origin `/api/*`
- Wraps `window.EventSource`: appends `?token=<JWT>` query param (SSE can't send headers)
- Clears localStorage on 401, redirects to `/login`

**Key flows:**
- Sign-up: email + password → POST `/api/auth/signup` → token stored → redirect `/dashboard`
- Sign-in: email + password → POST `/api/auth/login` → token stored → redirect `/dashboard`
- Forgot password: email → POST `/api/auth/request-password-reset` → (always returns 200) → check email
- Verify email: (auto on `/verify-email?token=X`) → POST `/api/auth/verify-email` → redirect `/dashboard`

---

## File Structure

```
/Users/naveenkumar/GeoClaw/
├── README.md                          # ~95 lines: quick start, stack, launch checklist
├── ENV_VARS.md                        # Environment variables guide (SMTP, JWT, DB, etc.)
├── dashboard_api.py                   # FastAPI app (1700+ lines); all /api/* endpoints
├── middleware/
│   └── auth.py                        # JWT verification, request.state.user_id, public routes
├── services/
│   ├── auth_service.py                # JWT token/verify, password hashing, email validation
│   ├── email_service.py               # stdlib SMTP wrapper (opt-in via SMTP_HOST)
│   ├── user_repository.py             # User CRUD, token lifecycle (issue/consume)
│   ├── tenant_scope.py                # scope_where(), and_scope() helpers
│   ├── terminal_ui_service.py         # SPA injection, auth shim, login page render
│   ├── db_helpers.py                  # SQLite/Postgres query routing
│   └── [signal/news/market/etc]       # Market data, signals, analysis engines
├── intelligence/
│   ├── db.py                          # DB init, create tables, schema
│   ├── jp225_predictor.py             # Nikkei 225 ML model
│   └── [*_predictor.py, *_engine.py]  # Quant/neural/backtest engines
├── ui/
│   ├── login.html                     # Sign-in + create account (tabs)
│   ├── forgot_password.html           # Request password reset email
│   ├── reset_password.html            # POST new password with reset token
│   ├── verify_email.html              # Auto-verify email with token
│   └── dashboard.html                 # Main SPA wrapper
├── static/
│   └── dashboard-app/assets/
│       └── index-HASH.js              # Compiled React bundle
├── tests/
│   ├── test_auth_and_tenant.py        # 20 tests: JWT, scoping, middleware, tokens
│   ├── test_multi_timeframe_analysis.py
│   └── test_neural_schema.py
└── sources/                           # Data adapters (Hyperliquid, etc.)
```

---

## Running Locally

### Setup

```bash
cd /Users/naveenkumar/GeoClaw
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# OR use the existing venv:
source /Users/naveenkumar/GeoClaw/venv/bin/activate
```

### Environment

Create `.env.geoclaw` (or export variables):

```bash
# Dev mode (SQLite)
export GEOCLAW_ENV=dev
export DATABASE_URL='sqlite:///geoclaw.db'

# Auth
export GEOCLAW_JWT_SECRET='dev-secret-min-32-chars-long-ok'

# Email (optional — logs to stdout if unset)
# export SMTP_HOST='smtp.sendgrid.net'
# export SMTP_PORT='587'
# export SMTP_USER='apikey'
# export SMTP_PASSWORD='SG.xxx'
# export SMTP_FROM='noreply@geoclaw.local'

# CORS origin
export GEOCLAW_PRODUCTION_ORIGIN='http://localhost:3000'

# LLM (optional for /api/briefing, /api/ask, /api/scenarios)
export GROQ_API_KEY='gsk_...'

# Data sources (optional)
export FRED_API_KEY='...'
export NEWSAPI_KEY='...'
```

### Run API

```bash
source venv/bin/activate
python3 dashboard_api.py
# Listens on http://localhost:8001
```

Visit:
- http://localhost:8001/ → main page (with auth shim injected)
- http://localhost:8001/login → sign-in page
- http://localhost:8001/forgot-password → password reset request
- http://localhost:8001/api/auth/signup → POST to create account

### Run tests

```bash
venv/bin/python3 tests/test_auth_and_tenant.py
# Output: 20 passed, 0 failed
```

---

## Deployment to Production

### Prerequisites

1. **Postgres database** (not SQLite — can't handle concurrent writes)
   ```bash
   createdb geoclaw
   ```

2. **Environment variables** in your hosting platform (Railway, Fly, Heroku, etc.):
   ```
   GEOCLAW_ENV=production
   DATABASE_URL=postgres://user:pass@host:5432/geoclaw
   GEOCLAW_JWT_SECRET=<64-char random secret>
   GEOCLAW_PRODUCTION_ORIGIN=https://geoclaw.example.com
   GROQ_API_KEY=<if using LLM endpoints>
   SMTP_HOST=<your relay>
   SMTP_PORT=587
   SMTP_USER=<username>
   SMTP_PASSWORD=<password>
   SMTP_FROM=noreply@geoclaw.example.com
   SMTP_FROM_NAME=GeoClaw
   ```

3. **Optional: legacy API token** for scheduled jobs / CLIs:
   ```
   GEOCLAW_LOCAL_TOKEN=<64-char random>
   ```
   Then CLI calls can use: `curl -H 'Authorization: Bearer <token>' https://geoclaw.example.com/api/signals`

### Deploy

**Railway / Fly / Heroku:**

1. Push branch to remote
2. Platform auto-detects Python, installs `requirements.txt`, runs `python3 dashboard_api.py`
3. On first deploy, database tables are created automatically (see `intelligence/db.py`)

**Docker (optional):**

```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python3", "dashboard_api.py"]
```

```bash
docker build -t geoclaw:latest .
docker run -p 8001:8001 -e DATABASE_URL='postgres://...' geoclaw:latest
```

### Health checks

```bash
# API running?
curl http://localhost:8001/

# Auth working?
curl -X POST http://localhost:8001/api/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"email":"test@example.com", "password":"testpass123"}'

# Rate limiting?
for i in {1..70}; do curl http://localhost:8001/api/signals; done
# 11th request onwards: HTTP 429
```

---

## Testing Checklist

### Unit tests
```bash
venv/bin/python3 tests/test_auth_and_tenant.py
# Expected: 20 passed
```

### Manual testing

**Auth flow:**
1. Open http://localhost:8001/login
2. Create account: email=`test@example.com`, password=`testpass123`
3. Check console network tab: `gc_access_token` stored in localStorage
4. Navigate to /dashboard: should load (SPA shim injected Authorization header)
5. Refresh: token still in localStorage, dashboard loads
6. Clear localStorage, refresh: redirected to /login

**Email verification:**
1. Sign up with SMTP unconfigured (logs to stdout)
2. Copy the verify token from logs
3. Visit `http://localhost:8001/verify-email?token=<token>`
4. Should see success, "Email verified"
5. Query DB: `SELECT email_verified_at FROM users WHERE email='test@example.com';` → should show timestamp

**Password reset:**
1. Visit http://localhost:8001/forgot-password
2. Enter email, submit
3. Check stdout for email with reset link + token
4. Visit `http://localhost:8001/reset-password?token=<token>`
5. Enter new password (min 8 chars), confirm
6. Should redirect to /login?reset=1 (shows "Password updated" confirmation)
7. Sign in with new password → should work

**Tenancy:**
1. User A signs up → gets token A
2. Create a signal (via `/api/signals` POST)
3. User B signs up → gets token B
4. User B tries `/api/signals` → should only see shared rows, NOT User A's signal
5. User A's signal has `user_id = A.id` in DB; User B's query scoped to `user_id IS NULL OR user_id = B.id`

**Rate limiting:**
```bash
# General /api/* endpoint: 60 requests / 60s per IP
for i in {1..70}; do curl http://localhost:8001/api/signals; done
# Requests 61-70 return HTTP 429

# LLM endpoints: 10 requests / 60s per IP
# (if GROQ_API_KEY set)
for i in {1..15}; do curl http://localhost:8001/api/briefing; done
# Requests 11-15 return HTTP 429
```

---

## Key Design Decisions

### Why row-level scoping instead of separate databases?
- **Multi-tenant efficiency**: shared compute, single codebase
- **Simpler operations**: one database to back up, one deployment pipeline
- **Easier feature parity**: updates roll out to all users at once
- **Trade-off**: requires discipline (always scope reads), but tests catch mistakes

### Why SHA-256 token hashing (not salted)?
- Tokens are already cryptographically random (32 bytes from `secrets.token_urlsafe`)
- Hashing is for "compromise of token table doesn't leak plaintext"
- Salt not needed because tokens are already entropy-rich and one-time use
- ✅ Server stores only hash; plaintext never persisted
- ✅ No functional login required once token is consumed

### Why PBKDF2-HMAC-SHA256 for passwords (not bcrypt)?
- Stdlib-only (no external C dependencies)
- 700k iterations by default (strong OWASP guidance)
- Good enough; bcrypt would require `pip install bcrypt`
- ✅ Passwords never logged, never sent in clear over HTTP (HTTPS enforced in prod)

### Why anti-enumeration on `/api/auth/request-password-reset`?
- Always returns HTTP 200, even if email not found
- Prevents attackers from discovering registered email addresses
- Downside: confused users may not know if email was sent
- ✅ Mitigated by UI hint: "Check your inbox (and spam)"

### Why LocalStorage for JWT (not HttpOnly cookie)?
- XSS risk, but mitigated:
  - SPA shim validates Origin header
  - No sensitive data in JWT payload (just `sub` = user_id)
- Benefit: SPA has transparent control over token lifecycle
- Trade-off: requires `HTTPS` in production (HTTP strips credentials)

---

## Common Operations

### Add a new API endpoint

1. **Define request/response models** in `dashboard_api.py`:
   ```python
   from pydantic import BaseModel
   
   class _MyRequest(BaseModel):
       field1: str
       field2: int
   ```

2. **Write the handler**, scope by user:
   ```python
   @app.post("/api/my-endpoint")
   async def api_my_endpoint(req: _MyRequest, request: Request):
       uid = current_user_id(request)
       # scope reads
       scope_clause, scope_params = scope_where(uid)
       # ... query with scope_clause ...
   ```

3. **Add to tests** (`tests/test_auth_and_tenant.py`):
   ```python
   def test_my_endpoint_scoped():
       # User A creates row
       # User B cannot see it
   ```

4. **Commit**:
   ```
   git commit -m "feat(api): add /api/my-endpoint with tenant scoping"
   ```

### Debug a test failure

```bash
venv/bin/python3 -m pytest tests/test_auth_and_tenant.py::test_name -vvs
# OR run file directly with assertions:
venv/bin/python3 tests/test_auth_and_tenant.py
```

Check:
- JWT secret consistency across tests
- Database state (tests use in-memory DB; check isolation)
- Timestamp comparisons (may need timezone handling)

### Handle a 401 Unauthorized in production

1. Check JWT_SECRET consistency across API instances
2. Verify token not expired: `datetime.now(timezone.utc) > claims['exp']`
3. Check CORS origin header matches `GEOCLAW_PRODUCTION_ORIGIN`
4. If SPA shim fails silently: check browser console for errors
5. Verify `/api/auth/login` endpoint is working (test curl POST)

### Enable a new LLM provider

1. Set env var (`OPENAI_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`)
2. Already supported in `dashboard_api.py` handlers for `/api/briefing`, `/api/ask`, `/api/scenarios`
3. No code change needed; routing is automatic based on which key is set

### Scale to N concurrent users

**Bottleneck checklist:**
- Postgres connection pool (tune `max_connections`)
- Rate limiter (hardcoded 60/60s general, 10/60s LLM; adjust in `middleware/rate_limit.py`)
- Row scoping performance (add index on `(user_id, created_at)` for big tables)
- JWT verification (cached in memory; per-request; negligible CPU)

**Load test:**
```bash
# Tools: wrk, locust, or Apache Bench
wrk -t4 -c100 -d30s http://localhost:8001/api/signals
```

---

## Remaining Work (nice-to-have, not blocking launch)

- [ ] HTML email templates (currently plain text)
- [ ] UI tests (Playwright / Cypress for auth flow)
- [ ] Monitoring dashboard (Prometheus metrics, Grafana)
- [ ] Audit log (track who accessed what data)
- [ ] Two-factor authentication (TOTP)
- [ ] Social login (Google OAuth, GitHub, etc.)
- [ ] Admin dashboard (manage users, view logs)
- [ ] Rate limit per-user (not just per-IP)
- [ ] Database backups automation
- [ ] CDN for static assets

---

## Support & Debugging

### Logs

**Dev (stdout):**
```
[email_service] SMTP not configured — would have sent to user@example.com
[dashboard_api] startup complete
```

**Prod (check platform logs):**
```bash
# Railway
railway logs

# Fly
fly logs

# Docker
docker logs <container_id>
```

### Common errors

| Error | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError: No module named 'requests'` | venv not activated | `source venv/bin/activate` |
| `psycopg2.OperationalError: could not connect to server` | Postgres not running / wrong URL | Check `DATABASE_URL` |
| `JWT_SECRET must be set` | Missing env var | Set `GEOCLAW_JWT_SECRET` |
| `403 Forbidden` (CORS) | Origin not in allow-list | Add to `GEOCLAW_PRODUCTION_ORIGIN` |
| `Email not received` | SMTP misconfigured | Check `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` |
| `Token expired` on reset-password | Link > 1 hour old | Issue new link via `/forgot-password` |
| `Row-level scope not working` | `scope_where()` not called | Grep endpoint for `current_user_id()` + `scope_where()` |

### Getting help

1. **Check tests**: `tests/test_auth_and_tenant.py` covers JWT, scoping, tokens, middleware
2. **Check middleware**: `middleware/auth.py` logs request.state on 401
3. **Check database schema**: `intelligence/db.py` defines tables and migrations
4. **Check UI**: browser DevTools Network tab shows `/api/*` requests + response bodies

---

## Summary: Public Launch Readiness

✅ **Go live:**
- Multi-tenant row scoping tested across /api/*
- Email verification + password reset working
- Auth pages polished and responsive
- SPA shim correctly injects tokens
- 20/20 tests passing
- ENV_VARS documented

⚠️ **Before going live:**
1. Set real `GEOCLAW_JWT_SECRET` (64 chars, `secrets.token_urlsafe(64)`)
2. Set real `GEOCLAW_PRODUCTION_ORIGIN` (your domain)
3. Set `DATABASE_URL` to Postgres (not SQLite)
4. Set `SMTP_*` vars for email delivery
5. Set at least one LLM API key (GROQ / OPENAI / GEMINI)
6. Run `tests/test_auth_and_tenant.py` in the deployment environment
7. Test sign-up → verify email → sign-in flow in production
8. Test password reset flow in production
9. Monitor logs for 48h post-launch

Good luck. 🚀

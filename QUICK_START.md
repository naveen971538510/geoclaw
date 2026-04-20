# GeoClaw Quick Start

## Local Development (5 minutes)

```bash
cd /Users/naveenkumar/GeoClaw
source venv/bin/activate

# Create .env.geoclaw with:
export GEOCLAW_ENV=dev
export DATABASE_URL='sqlite:///geoclaw.db'
export GEOCLAW_JWT_SECRET='dev-secret-32-chars-minimum-ok'
export GROQ_API_KEY='gsk_...'  # optional

# Run API
python3 dashboard_api.py
# Visit http://localhost:8001

# Run tests
python3 tests/test_auth_and_tenant.py
# Expected: 20 passed
```

## Production Checklist (before `git push`)

- [ ] `GEOCLAW_ENV=production` in platform env vars
- [ ] `DATABASE_URL` → Postgres (not SQLite)
- [ ] `GEOCLAW_JWT_SECRET` → 64-char random (generate: `python3 -c "import secrets; print(secrets.token_urlsafe(64))"`)
- [ ] `GEOCLAW_PRODUCTION_ORIGIN` → your domain
- [ ] `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` set
- [ ] At least one LLM key set (GROQ / OPENAI / GEMINI)
- [ ] Tests pass locally: `python3 tests/test_auth_and_tenant.py`
- [ ] Manual sign-up → verify email → sign-in flow tested

## Common Commands

| Task | Command |
|------|---------|
| Start API | `python3 dashboard_api.py` |
| Run tests | `python3 tests/test_auth_and_tenant.py` |
| Create test user | `curl -X POST http://localhost:8001/api/auth/signup -H 'Content-Type: application/json' -d '{"email":"test@example.com","password":"testpass123"}'` |
| Check JWT | `curl -X POST http://localhost:8001/api/auth/login -H 'Content-Type: application/json' -d '{"email":"test@example.com","password":"testpass123"}'` → copy `access_token` |
| Test authenticated endpoint | `curl -H 'Authorization: Bearer <token>' http://localhost:8001/api/signals` |
| Check database | `sqlite3 geoclaw.db ".schema"` (dev) or `psql $DATABASE_URL` (prod) |
| View logs (production) | `railway logs` (Railway) or `fly logs` (Fly) |
| Reset local database | `rm geoclaw.db` then restart `dashboard_api.py` |

## Architecture (60-second version)

**Frontend:**
- React SPA in `static/dashboard-app/`
- Auth shim (`services/terminal_ui_service.py`) auto-injects Bearer token on `/api/*` calls
- Four pages: login, forgot-password, reset-password, verify-email (all dark theme, cohesive design)

**Backend:**
- FastAPI (`dashboard_api.py`) with middleware stack: auth → rate-limit
- JWT verification in `middleware/auth.py` sets `request.state.user_id`
- Every `/api/*` handler calls `current_user_id(request)` and scopes reads via `scope_where(user_id)`
- Public endpoints: `/api/auth/{signup,login,verify-email,request-password-reset,reset-password}`

**Database:**
- Postgres in prod, SQLite in dev
- Row-level scoping: `NULL user_id` = shared, `user_id = X` = private to user X
- Auth tokens: one-time use, TTL-based, SHA-256 hashed

**Email:**
- Opt-in via `SMTP_HOST` env var
- Verification: user gets link with token → browser auto-verifies
- Password reset: same flow with 1-hour TTL

## Key Files

| File | Purpose |
|------|---------|
| `dashboard_api.py` | All /api/* endpoints + HTML page routes |
| `middleware/auth.py` | JWT verification, public route gating |
| `services/tenant_scope.py` | `scope_where()` and `and_scope()` helpers |
| `services/terminal_ui_service.py` | Auth shim injection into HTML |
| `services/email_service.py` | SMTP wrapper (stdlib only) |
| `services/user_repository.py` | User CRUD, token lifecycle |
| `intelligence/db.py` | Schema init, migrations |
| `ui/login.html` | Sign-in + create account |
| `ui/forgot-password.html` | Request reset email |
| `ui/reset-password.html` | Set new password with token |
| `ui/verify-email.html` | Verify email with token |
| `tests/test_auth_and_tenant.py` | 20 unit tests (JWT, scoping, tokens) |

## Debugging

**"401 Unauthorized" on API calls:**
1. Check `GEOCLAW_JWT_SECRET` matches across all instances
2. Check browser localStorage: `console.log(localStorage.getItem('gc_access_token'))`
3. Check token not expired: `curl -H 'Authorization: Bearer <token>' http://localhost:8001/api/auth/me`

**User can see another user's data:**
1. Grep the endpoint in `dashboard_api.py` for `current_user_id(request)`
2. Check it calls `scope_where(uid)` and applies the scope clause to the query
3. Add `scope_where()` if missing, add test to `test_auth_and_tenant.py`

**Email not sending:**
1. Check `SMTP_HOST` set in env vars
2. Check `SMTP_PORT` (usually 587 or 465)
3. Check `SMTP_USER` and `SMTP_PASSWORD` are correct
4. Dev mode: restart `dashboard_api.py` and check stdout for email body

**Tests failing:**
1. Check venv activated: `which python3` should show `.../venv/bin/python3`
2. Check `GEOCLAW_JWT_SECRET` set: `echo $GEOCLAW_JWT_SECRET`
3. Check database not locked: `pkill -f dashboard_api.py` and try again
4. Run single test: `python3 -c "import sys; sys.path.insert(0, '.'); from tests.test_auth_and_tenant import test_jwt_roundtrip; test_jwt_roundtrip()"`

## What's Ready for Launch

✅ Row-level multi-tenancy (tested across all `/api/*` reads)  
✅ Email verification flow (sign-up → verify email → dashboard)  
✅ Password reset flow (forgot-password → reset-password → re-sign-in)  
✅ SPA token injection (localStorage → Bearer header)  
✅ Auth pages (login, forgot-password, reset-password, verify-email)  
✅ 20/20 tests passing  
✅ Vendored all dependency modules (fresh clone boots)  
✅ ENV_VARS fully documented  

## Next After Launch

Consider for v2:
- Two-factor authentication (TOTP)
- Social login (OAuth)
- HTML email templates
- Per-user rate limits
- Audit log
- Admin dashboard

---

See [HANDOVER.md](HANDOVER.md) for full architecture, deployment, and operations guide.

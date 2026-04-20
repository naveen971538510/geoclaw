# GeoClaw — Operations Handover (Single Block)

## Stack
- **Backend:** FastAPI + Postgres (prod) or SQLite (dev)
- **Frontend:** vanilla HTML/JS + `localStorage` for tokens
- **Auth:** JWT HS256 (HMAC-SHA256, 1h TTL) + PBKDF2-SHA256 passwords (700k iter)
- **Multi-tenant:** nullable `user_id` column; row-level filtering via `scope_where(user_id)`
- **Deployment:** Fly.io, Python 3.12-slim Docker, shared-cpu-1x + 512MB RAM, TLS termination at edge

## Local Setup
1. `cd GeoClaw && python -m venv venv && source venv/bin/activate`
2. `pip install -r requirements.txt`
3. `export GEOCLAW_JWT_SECRET="test-$(python -c 'import secrets; print(secrets.token_hex(30))')"` (or use 61-char test key)
4. `export GEOCLAW_ALLOW_LOCALHOST_AUTH=1` (localhost auth opt-in for dev)
5. `python dashboard_api.py` → http://localhost:8000 (FastAPI serves UI + API)
6. In browser: http://localhost:8000 → sign up → login → dashboard
7. Run tests: `python -m pytest tests/test_auth_and_tenant.py -v` (30 tests)

## Production Checklist
- [ ] Set `GEOCLAW_JWT_SECRET` (61+ chars, random)
- [ ] Set `GEOCLAW_SMTP_HOST`, `GEOCLAW_SMTP_USER`, `GEOCLAW_SMTP_PASSWORD` (or omit for no email)
- [ ] **Do NOT set** `GEOCLAW_ALLOW_LOCALHOST_AUTH` (defaults to deny; only set if you have a sidecar)
- [ ] Set `GEOCLAW_TRUSTED_PROXIES="127.0.0.1"` (Fly edge proxy IP) to trust X-Forwarded-For
- [ ] Provision empty Postgres database, set `DATABASE_URL`
- [ ] `fly deploy` or push Docker image to your registry
- [ ] Verify `curl -f https://app.fly.dev/` returns 200

## Common Operations

### Create admin (one-time)
```bash
python -c "
from services.user_repository import create_user
from services.auth_service import hash_password
user = create_user('admin@example.com', hash_password('SecureAdminPassword123!'), display_name='Admin')
print(f'Created user_id={user[0]}')
"
```

### Reset user password (admin CLI)
```bash
python -c "
from services.user_repository import create_reset_token
import sys
email = sys.argv[1]  # e.g., user@example.com
token = create_reset_token(email)
print(f'Reset link: http://localhost:8000/reset-password?token={token}')
" user@example.com
```

### View database schema
```bash
python -c "from intelligence.db import ensure_tables; ensure_tables()" && python -c "
import sqlite3; conn = sqlite3.connect(':memory:'); from intelligence.db import _create_tables_sqlite; _create_tables_sqlite(conn); 
import inspect; print(inspect.getsource(_create_tables_sqlite))
"
```

### Email test (if SMTP configured)
```bash
python -c "
from services.email_service import send_email
send_email('test@example.com', 'Test Subject', 'Test body')
print('Email sent (check spam)')
"
```

## Files at a Glance
| File | Purpose |
|------|---------|
| `dashboard_api.py` | FastAPI entry point; mounts middleware + routes auth/data endpoints |
| `middleware/auth.py` | JWT + legacy token validation; localhost auth opt-in; public path allowlist |
| `middleware/rate_limit.py` | 100 req/min default, 20 for /api/news (expensive); X-Forwarded-For trust via TRUSTED_PROXIES |
| `services/auth_service.py` | Password hashing (PBKDF2), JWT, email validation, length caps |
| `services/user_repository.py` | User CRUD, token lifecycle (issue/consume), atomic password reset flow |
| `services/email_service.py` | SMTP send, header/CRLF injection hardening, TLS/SSL enforcement |
| `services/tenant_scope.py` | `scope_where(user_id)` → SQL clause; `and_scope()` for chaining |
| `intelligence/db.py` | Schema init, multi-dialect (Postgres %s / SQLite ?) |
| `ui/login.html` | Sign-in + signup tabs, forgot-password link, localStorage check |
| `ui/forgot_password.html` | Email request for reset link |
| `ui/reset_password.html` | Consume reset token, update password, strip token from URL |
| `ui/verify_email.html` | Consume verification token, state machine (loading/ok/err/missing) |
| `Dockerfile` | Python 3.12-slim, runs `dashboard_api.py` only (main.py legacy, not exposed) |
| `fly.toml` | Fly config, :8000 internal, HTTPS enforced, 1 shared-cpu-1x machine |

## Key Design Decisions

**Multi-tenant row filtering:** Every data query calls `scope_where(user_id)` or is explicitly marked public. User `NULL` rows are visible to anonymous users; owned rows only to the user. ✓ No main.py endpoints exposed in prod.

**Password hashing:** PBKDF2 (not bcrypt) for portability; 700k iterations; max 1024 chars input to prevent DoS. Password change invalidates outstanding reset tokens.

**JWT:** 1-hour TTL, HS256 (stdlib hmac/hashlib). No session revocation on password change (acceptable for short TTL); documented as future improvement.

**Email tokens:** SHA-256 one-time tokens, plaintext emailed, hash stored. Consumed atomically via `UPDATE … WHERE consumed_at IS NULL RETURNING` (no SELECT FOR UPDATE race).

**Rate limit:** Per-IP buckets, X-Forwarded-For only trusted from an allowlist (GEOCLAW_TRUSTED_PROXIES). Expensive endpoints (/api/news) get stricter limits.

**Localhost auth:** Disabled by default in prod. Opt-in via `GEOCLAW_ALLOW_LOCALHOST_AUTH=1` only for sidecars/dev. Prevents accidental open access.

**Public paths:** /api/auth/login, /api/auth/signup, /api/auth/request-password-reset, /forgot-password, /reset-password, /verify-email are unauthenticated.

**EventSource (SSE) tokens:** `?token=` appended only on /api/stream paths, not all API endpoints. Prevents Referer leakage to third-party analytics.

**UI hardening:** `<meta name="referrer" content="no-referrer">` on auth pages; `history.replaceState` strips tokens from URL after read; `localStorage` failure detection prevents redirect loops.

## Debugging

**Test a login flow:**
```bash
# 1. Signup
curl -X POST http://localhost:8000/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"test@x.com","password":"TestPass123!","display_name":"Test"}'

# 2. Grab token from response
TOKEN="eyJ..."

# 3. Use token
curl http://localhost:8000/api/dashboard \
  -H "Authorization: Bearer $TOKEN"
```

**JWT decode (without verification):**
```bash
python -c "
import json, base64
tok = 'eyJ...'
parts = tok.split('.')
payload = base64.urlsafe_b64decode(parts[1] + '==')
print(json.loads(payload))
"
```

**Database queries (dev):**
```bash
sqlite3 intelligence.db "SELECT id, email, display_name FROM users LIMIT 5;"
```

**Logs in Docker (Fly):**
```bash
fly logs -a geoclaw --follow
```

**Rate-limit state (dev, in-memory):**
```bash
# Buckets decay after inactivity; no persistent state
python -c "
from middleware.rate_limit import EXPENSIVE_LIMIT, DEFAULT_LIMIT
print(f'Expensive: {EXPENSIVE_LIMIT[0]} req/{EXPENSIVE_LIMIT[1]}s')
print(f'Default:   {DEFAULT_LIMIT[0]} req/{DEFAULT_LIMIT[1]}s')
"
```

## Deferred / Known Issues
See **AUDIT_FINDINGS.md** for:
- Session revocation on password change (JWT token TTL short; acceptable for launch)
- `main.py` endpoints unscoped (not exposed in prod; don't re-expose)
- Agent jobs run anonymous (no user_id propagation)
- Stripe checkout missing user reconciliation
- Signup email enumeration (returns 409 taken; UX tradeoff)

## Next Operator Tasks
1. Confirm `fly deploy` succeeds and health check passes
2. Test signup + login on prod
3. Monitor `fly logs -a geoclaw` for errors (esp. SMTP)
4. Document any custom sidecar config (main.py, cron, etc.) in your runbook

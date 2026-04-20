# GeoClaw — Operations Handover (Single Block)

This doc describes the **shipped local-Mac product**. It auto-starts on login,
backs up nightly, mirrors offsite to iCloud, and exposes one health-check
command. The original Fly/Docker path is kept at the bottom as an optional
future if you ever want to host it publicly.

---

## Stack
- **Backend:** FastAPI + Postgres 14 (Homebrew) — `dashboard_api.py`
- **Frontend:** vanilla HTML/JS; JWT in `localStorage`
- **Auth:** JWT HS256 (1h TTL) + PBKDF2-SHA256 (700k iter, 1024-char cap)
- **Multi-tenant:** nullable `user_id` column; `scope_where(user_id)` on every data query
- **Deployment:** runs on this Mac via `launchd` LaunchAgent; no cloud, no account, no cost

## What's installed on this machine

| Component | Label / path | Behaviour |
|---|---|---|
| Auto-start agent | `~/Library/LaunchAgents/com.geoclaw.local.plist` | Boots at login, keeps alive on crash |
| Nightly backup agent | `~/Library/LaunchAgents/com.geoclaw.backup.plist` | 03:30 daily `pg_dump → gzip` |
| Server log | `<repo>/.geoclaw.log` | stdout/stderr of running server |
| Backup log | `<repo>/.geoclaw-backup.log` | stdout/stderr of nightly backup |
| Local backups | `~/Documents/GeoClaw-Backups/` | 14-day retention |
| Offsite mirror | `~/Library/Mobile Documents/com~apple~CloudDocs/GeoClaw-Backups/` | Apple syncs off-machine automatically |
| Database | Postgres: `geoclaw` | Homebrew `postgresql@14` service |
| App config | `<repo>/.env.local` | `GEOCLAW_JWT_SECRET`, `DATABASE_URL` — gitignored |

## Daily Ops

```bash
./status.sh              # one-shot health check: Postgres, agents, HTTP, backups
./scripts/backup.sh      # manual backup right now
./scripts/restore.sh     # restore newest backup (prompts "yes" to confirm)
./scripts/verify-backup.sh  # prove newest backup is actually restorable

tail -f .geoclaw.log     # live server logs
tail .geoclaw-backup.log # most recent backup log
```

## Install / Uninstall

```bash
# First install on a fresh machine (idempotent — safe to re-run)
./run-local.sh              # one-time: creates .env.local, ensures DB
./install-autostart.sh      # installs com.geoclaw.local LaunchAgent
./install-backup.sh         # installs com.geoclaw.backup LaunchAgent

# Uninstall
./install-autostart.sh --uninstall
./install-backup.sh --uninstall
```

## If something breaks

| Symptom | Fix |
|---|---|
| `./status.sh` says Postgres not running | `brew services start postgresql@14` |
| Server not responding on :8000 | `tail -40 .geoclaw.log`; then `launchctl kickstart -k gui/$(id -u)/com.geoclaw.local` |
| "DB is corrupted / need to roll back" | `./scripts/restore.sh` picks newest from local or iCloud |
| "Backups look wrong" | `./scripts/verify-backup.sh` runs a live-vs-backup row-count diff |
| Forgot JWT secret | `.env.local` in the repo root. **Back it up** — losing it invalidates all sessions |

## Common Operations

### Create user (CLI)
```bash
source .env.local && python -c "
from services.user_repository import create_user
from services.auth_service import hash_password
user = create_user('you@example.com', hash_password('SecurePass123!'), display_name='You')
print(f'Created user_id={user[0]}')
"
```

### Reset password (CLI, bypasses email)
```bash
source .env.local && python -c "
from services.user_repository import create_reset_token
token = create_reset_token('you@example.com')
print(f'http://localhost:8000/reset-password?token={token}')
"
```

## Tests
```bash
python -m pytest tests/test_auth_and_tenant.py -v    # 30 auth/tenant tests
```

## Files at a Glance
| File | Purpose |
|------|---------|
| `dashboard_api.py` | FastAPI entry point; mounts middleware + routes auth/data endpoints |
| `run-local.sh` | Dev/local runner: ensures Postgres, creates `.env.local`, starts server |
| `GeoClaw.command` | Double-clickable Finder shim → `run-local.sh` |
| `install-autostart.sh` | Installs/uninstalls the auto-start LaunchAgent |
| `install-backup.sh` | Installs/uninstalls the nightly backup LaunchAgent |
| `status.sh` | One-shot health check across all 6 local components |
| `scripts/backup.sh` | `pg_dump → gzip → ~/Documents + iCloud mirror`; 14-day retention |
| `scripts/restore.sh` | Restore newest (or named) snapshot; picks newest across local + iCloud |
| `scripts/verify-backup.sh` | Restores into scratch DB, row-count diff vs live |
| `middleware/auth.py` | JWT + legacy token validation; public-path allowlist |
| `middleware/rate_limit.py` | 100 req/min default, 20 for /api/news; `GEOCLAW_TRUSTED_PROXIES` gates X-Forwarded-For |
| `services/auth_service.py` | Password hashing, JWT, email validation, length caps |
| `services/user_repository.py` | User CRUD, atomic token lifecycle |
| `services/email_service.py` | SMTP with header/CRLF hardening + TLS enforcement |
| `services/tenant_scope.py` | `scope_where(user_id)` → SQL clause |
| `intelligence/db.py` | Schema init (Postgres + SQLite dialects) |

## Key Design Decisions

**Multi-tenant row filtering:** every data query calls `scope_where(user_id)` or is explicitly public. `NULL` rows are visible to anonymous users; owned rows only to the user. `main.py` is NOT exposed — it has unscoped endpoints.

**Password hashing:** PBKDF2-SHA256, 700k iterations, 1024-char input cap. Password change consumes outstanding reset tokens.

**JWT:** 1h TTL, HS256. No session revocation on password change — acceptable at this TTL. Secret in `.env.local` persists across restarts.

**Email tokens:** SHA-256 one-time tokens; hash stored, plaintext emailed once. Atomic consume via `UPDATE ... WHERE consumed_at IS NULL RETURNING`.

**Rate limit:** per-IP buckets. `GEOCLAW_TRUSTED_PROXIES` allowlist gates `X-Forwarded-For` honouring — prevents bucket-rotation bypass.

**Localhost auth:** denied by default. Opt-in via `GEOCLAW_ALLOW_LOCALHOST_AUTH=1` only.

**Public paths:** `/api/auth/login`, `/api/auth/signup`, `/api/auth/request-password-reset`, `/forgot-password`, `/reset-password`, `/verify-email`.

**UI token hygiene:** `<meta name="referrer" content="no-referrer">` on auth pages; `history.replaceState` strips `?token=` from URL after read.

## Debugging

**Smoke the login flow:**
```bash
curl -X POST http://localhost:8000/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"test@x.com","password":"TestPass123!","display_name":"Test"}'
# → grab token, then:
curl http://localhost:8000/api/dashboard -H "Authorization: Bearer $TOKEN"
```

**Decode a JWT (unverified):**
```bash
python -c "import json,base64; p=(input().split('.')[1]); print(json.loads(base64.urlsafe_b64decode(p+'==')))"
```

**Query the DB:**
```bash
/opt/homebrew/opt/postgresql@14/bin/psql geoclaw -c "SELECT id, email, display_name FROM users;"
```

## Deferred / Known Issues
See `AUDIT_FINDINGS.md` for the full list. In a **single-user local context** every deferred item is either not applicable (main.py not exposed, Stripe unused, SQLite path unused, one-IP rate limit, enumeration irrelevant with one user) or low-risk (short JWT TTL covers session revocation). None are blockers for this deployment.

## Recovery from total data loss

1. **Lost this laptop:** log in to `iCloud.com` on any Mac → Drive → `GeoClaw-Backups/` → download newest `geoclaw-*.sql.gz`.
2. **New Mac setup:** clone repo, `./run-local.sh`, then `./scripts/restore.sh /path/to/downloaded.sql.gz`.
3. **Lost `.env.local` (JWT secret):** all existing sessions invalidate; users must log in again. Password hashes in DB still work. Generate a new secret: `python -c "import secrets; print('GEOCLAW_JWT_SECRET='+secrets.token_hex(30))"` → write to `.env.local`.

---

## Appendix — Publishing to the internet (optional, deferred)

The repo still contains `Dockerfile`, `fly.toml`, `deploy.sh`, `render.yaml`, and `RENDER_DEPLOY.md` from an earlier pivot. If you ever want a public instance:

- **Fly.io** — `./deploy.sh` handles auth, secret generation, health check, smoke test. Requires `flyctl auth login` and (currently) a credit card for a free-tier machine.
- **Render + Neon** — `render.yaml` is a Blueprint; `RENDER_DEPLOY.md` has the 10-minute walkthrough. Free tier, no card required.

For production you must:
- [ ] Set `GEOCLAW_JWT_SECRET` (61+ chars)
- [ ] Provision an empty Postgres DB, set `DATABASE_URL`
- [ ] **Do NOT** set `GEOCLAW_ALLOW_LOCALHOST_AUTH`
- [ ] Set `GEOCLAW_TRUSTED_PROXIES` to the edge proxy IP
- [ ] Configure SMTP (`GEOCLAW_SMTP_*`) or accept no email flows

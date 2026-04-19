# Security Policy

## Reporting a vulnerability

Please do **not** open public GitHub issues for security-sensitive reports.

Instead, email the maintainers at `swanomyfriend@gmail.com` with:

- a short description of the issue,
- reproduction steps,
- the affected commit SHA or tag, and
- the impact you believe the issue has.

You should expect an acknowledgement within five business days. Please
give us a reasonable window to investigate and ship a fix before any
public disclosure.

## Scope

In-scope for this repository:

- The FastAPI application in `main.py` and all `/api/*` routes.
- The dashboard API in `dashboard_api.py`.
- The Telegram bot in `services/telegram_bot.py` and the webhook at
  `POST /api/telegram/webhook`.
- The SQLite schema and migration code in `migration.py` / `db.py`.
- Everything under `services/` and `sources/`.

Out of scope:

- Social engineering of the maintainer.
- Denial-of-service that requires massive request volume against a
  self-hosted deployment.
- Reports that depend on a pre-compromised host (e.g. an attacker who
  already has shell access or can edit the environment).

## Secrets handling

- Runtime secrets (API keys, bot tokens, SMTP credentials) are read
  from environment variables — see `.env.geoclaw.example` and
  `ENV_VARS.md`.
- Never commit a real `.env.geoclaw` / `.bot_token` / `*.secret` /
  `*.secrets` file. `.gitignore` blocks those patterns.
- If you believe a token has been committed to history, revoke it at
  the provider immediately, then open a private report so we can
  coordinate a `git filter-repo` rewrite.

## Known-good hardening

This repository does the following by default:

- `hmac.compare_digest` is used for every token comparison on
  mutating `/api/*` routes.
- Cross-origin responses on the SSE stream endpoint honour an explicit
  allow-list (`GEOCLAW_PRODUCTION_ORIGIN` + localhost) and never emit
  `Access-Control-Allow-Origin: *`.
- DDL helpers in `migration.py` validate table/column identifiers
  against `^[A-Za-z_][A-Za-z0-9_]*$` before interpolating into
  `ALTER TABLE` statements.
- `POST /api/telegram/webhook` requires the
  `X-Telegram-Bot-Api-Secret-Token` header (compared with
  `hmac.compare_digest`) when `TELEGRAM_WEBHOOK_SECRET` is configured.
- CI runs `ruff`, `bandit`, and `pip-audit` on every push and PR.

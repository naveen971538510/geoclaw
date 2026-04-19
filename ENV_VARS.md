# GeoClaw environment variables

Variables referenced across the codebase (set in Railway, `.env`, or `.env.geoclaw` as appropriate).

## Required for production

| Variable | Description |
|----------|-------------|
| `GEOCLAW_ENV` | Set to `production` (or `prod`) to enable production guards. When set, the app refuses to start without `DATABASE_URL` and rejects `GEOCLAW_DB_BACKEND=sqlite`. Leave unset for local dev. |
| `DATABASE_URL` | PostgreSQL connection string. **Required in production** — SQLite cannot handle concurrent writes from multiple users. |
| `GEOCLAW_JWT_SECRET` | Signing key for user JWT access tokens. **Required in any multi-user deployment.** Minimum 32 chars. Generate with `python3 -c "import secrets; print(secrets.token_urlsafe(64))"`. If unset, `/api/auth/signup` and `/api/auth/login` return 500. |

## Strongly recommended for public launch

| Variable | Description |
|----------|-------------|
| `GEOCLAW_LOCAL_TOKEN` | Legacy shared bearer token. Useful as a fallback for scheduled jobs and CLIs. Any non-localhost request without a valid JWT must present this. Leave unset once all clients migrate to JWT. |
| `GEOCLAW_PRODUCTION_ORIGIN` | Your public origin (e.g. `https://geoclaw.example.com`) — added to the CORS allow-list. |
| `PORT` | HTTP port for `dashboard_api.py` when hosted (Railway/Fly injects this); defaults to `8001`. |

## LLM providers (at least one needed for briefings / ask / scenarios)

| Variable | Description |
|----------|-------------|
| `GROQ_API_KEY` | Groq Cloud API key for LLM calls (`groq_briefing.py`, `news_agent`, briefings, scenarios). |
| `GROQ_MODEL` | Override default Groq model (default `llama-3.1-8b-instant`). |
| `GEMINI_API_KEY` | Google AI (Gemini) API key; used when Groq fails or returns non-200. |
| `OPENAI_API_KEY` | OpenAI for article/thesis analysis (`config.py`, `main.py`). |
| `OPENAI_MODEL` | OpenAI model name override. |

## Data sources (optional — each enables a feature)

| Variable | Description |
|----------|-------------|
| `POSTGRES_URL` | Alternative Postgres URL; `intelligence/db.py` falls back if `DATABASE_URL` is unset. |
| `FRED_API_KEY` | St. Louis Fed FRED API key (`sources/macro_agent.py`). |
| `NEWSAPI_KEY` | NewsAPI.org key (optional — enables richer news ingest). |
| `GUARDIAN_API_KEY` | Guardian API key (optional). |
| `ALPHAVANTAGE_KEY` | Alpha Vantage key (optional). |
| `NFP_EXPECTED_MOM_K` | Expected NFP month-over-month change in thousands for signal scoring (`intelligence/signal_engine.py`). |
| `GEOCLAW_DAILY_BRIEFING_TZ` | Timezone name for legacy daily signal brief scheduling (if used). |

## Telegram bot (optional)

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token (`telegram_bot.py`). |
| `TELEGRAM_CHAT_ID` | Default chat for proactive sends. |
| `GEOCLAW_DASHBOARD_BRIEFING_URL` | Override GET URL for `/briefing` command (default `http://127.0.0.1:8001/api/briefing`). |
| `GEOCLAW_DASHBOARD_NEWS_URL` | Override GET URL for `/news`. |
| `GEOCLAW_DASHBOARD_SCENARIOS_URL` | Override GET URL for `/scenarios`. |
| `GEOCLAW_API_ASK_URL` | Base URL for natural-language queries. |

## Rate limits (hard-coded; document for reference)

- `/api/*` general: **60 requests / 60s per IP** (see `middleware/rate_limit.py`).
- LLM-backed endpoints (`/api/ask`, `/api/briefing`, `/api/scenarios`, `/api/stream`, `/api/news`, `/api/llm`, `/api/agent`): **10 requests / 60s per IP**.
- 429 responses include a `Retry-After` header.

## Minimum "public-launch" env checklist

```bash
export GEOCLAW_ENV=production
export DATABASE_URL='postgres://user:pass@host:5432/geoclaw'
export GEOCLAW_JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"
export GEOCLAW_PRODUCTION_ORIGIN='https://your-public-domain.example'
export GROQ_API_KEY='...'   # or OPENAI_API_KEY / GEMINI_API_KEY
```

Deployment processes (Procfile):

- **api**: `python3 dashboard_api.py` (listens on port **8001** by default).
- **bot**: `python3 telegram_bot.py`

# GeoClaw environment variables

Variables referenced across the codebase (set in Railway, `.env`, or `.env.geoclaw` as appropriate).

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string (primary). Used by dashboard API, intelligence agents, Telegram DB commands, `news_agent`. |
| `POSTGRES_URL` | Alternative Postgres URL; `intelligence/db.py` falls back if `DATABASE_URL` is unset. |
| `GROQ_API_KEY` | Groq Cloud API key for LLM calls (`groq_briefing.py`, `news_agent`, briefings, scenarios). |
| `GEMINI_API_KEY` | Google AI (Gemini) API key; used when Groq fails or returns non-200 (`groq_briefing.py`). |
| `GROQ_MODEL` | Override default Groq model (default `llama-3.1-8b-instant`). |
| `FRED_API_KEY` | St. Louis Fed FRED API key (`sources/macro_agent.py`). |
| `NFP_EXPECTED_MOM_K` | Expected NFP month-over-month change in thousands for signal scoring (`intelligence/signal_engine.py`). |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token (`telegram_bot.py`, `services/telegram_bot.py`). |
| `TELEGRAM_CHAT_ID` | Default chat for proactive sends (`services/telegram_bot.py`, `agent.py`). |
| `GEOCLAW_DASHBOARD_BRIEFING_URL` | Override GET URL for `/briefing` command (default `http://127.0.0.1:8001/api/briefing`). |
| `GEOCLAW_DASHBOARD_NEWS_URL` | Override GET URL for `/news` (default `http://127.0.0.1:8001/api/news`). |
| `GEOCLAW_DASHBOARD_SCENARIOS_URL` | Override GET URL for `/scenarios` (default `http://127.0.0.1:8001/api/scenarios`). |
| `GEOCLAW_API_ASK_URL` | Base URL for natural-language queries (default `http://127.0.0.1:8001/api/ask` in `telegram_bot.py`; main app often on port 8000). |
| `GEOCLAW_PRODUCTION_ORIGIN` | Extra allowed CORS origin added to the SSE stream allow-list (`main.py::_STREAM_ALLOWED_ORIGINS`) and to `dashboard_api.py`. Exact scheme + host, no trailing slash (e.g. `https://app.example.com`). When set, it's the ONLY non-localhost origin browsers will receive `Access-Control-Allow-Origin` for on `/api/events/stream`; leave unset to keep the allow-list at localhost only. |
| `GEOCLAW_LOCAL_TOKEN` | Shared secret for `_mutation_guard` in `main.py` and the Bearer auth middleware in `dashboard_api.py`. When set, non-loopback callers must present it via the `x-geoclaw-token` header, `?token=` query param, or `Authorization: Bearer <token>`; comparison is constant-time via `hmac.compare_digest`. When empty, every mutation endpoint is **localhost-only**. Generate with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`. |
| `TELEGRAM_WEBHOOK_SECRET` | Optional shared secret that hardens `POST /api/telegram/webhook`. When set, every request must include header `X-Telegram-Bot-Api-Secret-Token: <secret>` (constant-time compare) or the endpoint returns 401. Configure it at `setWebhook` time via the `secret_token` parameter so genuine Telegram deliveries include the header automatically. When empty, the endpoint falls back to unauthenticated behaviour (backward-compatible). |
| `GEOCLAW_GUARD_READ_API` | Opt-in flag that extends the localhost-or-token guard to every `/api/*` request in `main.py` (see `_read_api_guard_middleware`) — every non-OPTIONS method (GET, POST, DELETE, …), not just GET. Accepts `1` / `true` / `yes` / `on`. Default **off** for backward compatibility with LAN/reverse-proxy setups. When on, non-loopback callers must present `GEOCLAW_LOCAL_TOKEN` via the `x-geoclaw-token` header, `Authorization: Bearer <token>`, or `?token=` query param; `/api/events/stream` and `/api/telegram/webhook` stay exempt because they have their own Origin allow-list / secret-token guards. POST /api/ask (a read endpoint that happens to be POST because it takes a JSON body) is intentionally covered. Mutation routes that already go through `_mutation_guard` are also covered — the double-check is redundant but harmless. Leave unset on pure-localhost deploys where the existing bind already provides the boundary. |
| `OPENAI_API_KEY` | OpenAI for article/thesis analysis (`config.py`, `main.py`). |
| `OPENAI_MODEL` | OpenAI model name override. |
| `NEWSAPI_KEY` | NewsAPI.org key if enabled. |
| `GUARDIAN_API_KEY` | Guardian API key if enabled. |
| `ALPHAVANTAGE_KEY` | Alpha Vantage key if enabled. |
| `GEOCLAW_DAILY_BRIEFING_TZ` | Timezone name for legacy daily signal brief scheduling (if used). |

| `PORT` | HTTP port for `dashboard_api.py` when hosted (Railway sets this); defaults to `8001`. |

Railway / Procfile processes:

- **api**: `python3 dashboard_api.py` (listens on port **8001** by default; set `PORT` if your host injects it — you may need to map `PORT` to uvicorn in a follow-up).
- **bot**: `python3 telegram_bot.py`

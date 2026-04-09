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
| `GEOCLAW_PRODUCTION_ORIGIN` | Extra allowed CORS origin for `dashboard_api.py`. |
| `GEOCLAW_LOCAL_TOKEN` | Optional local mutation guard token (`main.py`). |
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

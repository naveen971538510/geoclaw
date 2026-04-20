# GeoClaw on Render + Neon — free, no credit card

Total time: ~10 minutes. Monthly cost: $0.

## 1. Create a Neon Postgres database (2 min)

1. Go to https://neon.tech → **Sign up** (GitHub/Google login works).
2. Create a new project (any name, e.g. `geoclaw`).
3. On the project dashboard, find the **Connection string** box.
   Copy the string that starts with `postgres://` — it looks like:
   ```
   postgres://geoclaw_owner:xxxxx@ep-cool-forest-12345.eu-central-1.aws.neon.tech/neondb?sslmode=require
   ```
4. Keep this tab open — you'll paste it into Render in step 3.

**Free tier:** 3 GB storage, always-on (no cold start).

## 2. Push your code to GitHub (already done)

Branch `claude/heuristic-germain-5e8931` is pushed. Before deploying:

```bash
# Merge the hardening branch to main so Render deploys the secure version:
cd /Users/naveenkumar/GeoClaw/.claude/worktrees/heuristic-germain-5e8931
git checkout main
git merge claude/heuristic-germain-5e8931
git push origin main
```

(Or open a PR and merge in GitHub's UI: https://github.com/naveen971538510/geoclaw/pull/new/claude/heuristic-germain-5e8931)

## 3. Deploy on Render (3 min)

1. Go to https://render.com → **Sign up** (GitHub login recommended — Render reads your repos).
2. Dashboard → **New +** → **Blueprint**.
3. Connect the `geoclaw` repository. Render finds `render.yaml` automatically.
4. Render shows the proposed service with a list of env vars. Fill in:
   - `DATABASE_URL` → paste the Neon connection string from step 1
   - *(optional)* `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` if you want email
5. Click **Apply**. Render builds + deploys. First build ~3 min (installing torch-free deps).

When it finishes, you get a URL like `https://geoclaw.onrender.com`.

## 4. Initialize the database schema (1 min)

One-time — run locally against the Neon database:

```bash
cd /Users/naveenkumar/GeoClaw/.claude/worktrees/heuristic-germain-5e8931
export DATABASE_URL="<your Neon connection string>"
/Users/naveenkumar/GeoClaw/venv/bin/python3 -c "
from intelligence.db import ensure_intelligence_schema
ensure_intelligence_schema()
print('schema ready')
"
```

## 5. Smoke test the live site (1 min)

```bash
URL="https://geoclaw.onrender.com"  # your actual Render URL

# Health
curl -f "$URL/" && echo "✓ up"

# Signup
curl -s -X POST "$URL/api/auth/signup" \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"SecurePass123!"}'

# Open in browser
open "$URL/login"
```

Create an account, log in, you're live.

## Known tradeoffs of the Render free tier

| | Render Free | Fly Hobby |
|---|---|---|
| Monthly cost | $0 | ~$0–5 |
| Credit card | No | Yes (at signup) |
| Cold start | ~30s after 15min idle | None (1 always-on machine) |
| RAM | 512 MB | 256–512 MB |
| Regions | Oregon / Frankfurt / Singapore / Ohio | Any of ~30 |
| Auto-deploy on push | Yes | Via GitHub Action |

**Cold start explanation:** after 15 minutes of no HTTP traffic, Render spins the container down. The next request wakes it in ~30 seconds. Users see a brief spinner. At launch-scale (dozens of users/day), this is fine. If you're getting consistent traffic, Render's $7/mo Starter plan removes it.

## Upgrading later

- **Render Starter ($7/mo):** always-on, no cold start, 512 MB → 1 GB.
- **Neon Scale ($19/mo):** branching, point-in-time restore.
- **Custom domain:** Render → Settings → Custom Domains → paste CNAME. Free, auto-TLS.

## Troubleshooting

**Build fails on `pip install`:** check the Render log — likely a package that needs a system library. Add to `render.yaml` under `buildCommand`:
```yaml
buildCommand: apt-get update && apt-get install -y libpq-dev && pip install -r requirements.txt
```

**DATABASE_URL connection refused:** Neon pauses compute after 5 min idle on free tier. First connection takes ~1s to wake. This is fine in practice but if you see errors, upgrade to Neon Scale or add retry logic.

**Emails not sending:** check `SMTP_*` env vars in Render dashboard. If they're missing, the app logs "email not sent (SMTP not configured)" — signup still works.

**Redirect loop on /dashboard:** localStorage is blocked in the browser (private mode?). Our login page now surfaces this explicitly instead of looping.

## What to do next

- Point a custom domain
- Set up Render → Slack/email alerts on deploy failure
- Enable Neon daily backup (free tier includes 7-day point-in-time)
- Turn on SMTP when you're ready to send real verification emails

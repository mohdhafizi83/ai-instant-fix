# AI Instant Fix 🔧

Browser widget for submitting AI-powered instant fixes. Write what you want changed on any webpage — an AI agent executes it autonomously.

## How It Works

1. **Admin clicks widget** on any webpage → types a fix request (e.g. "Change the Save button to blue")
2. **Widget sends** `{prompt, url, user_id}` to the API server
3. **API server stores** in SQLite + forwards to Telegram Hermes Agent
4. **Hermes Agent** resolves file paths, edits files, verifies, marks complete
5. **Widget polls** task list → shows updated status (📋 accepted → ⏳ ongoing → ✅ completed)

No framework adapters needed. The AI agent discovers files autonomously.

## Quickstart

### 1. Start API Server

```bash
cd server
python3 -m venv venv && source venv/bin/activate
pip install flask flask-cors requests

# Get bot token from Hermes config
export TELEGRAM_BOT_TOKEN=***
export TELEGRAM_CHAT_ID=your_chat_id

python app.py  # runs on http://0.0.0.0:5555
```

### 2. Inject Widget

Add to any page:

```html
<script src="http://your-server:5555/static/ai-instant-fix.js"
  data-aif-api="http://your-server:5555"
  data-aif-user-id="admin@example.com">
</script>
```

Or copy `widget/ai-instant-fix.js` + `widget/ai-instant-fix.css` to your project.

### 3. WordPress

Copy `wp-plugin/` to `/wp-content/plugins/ai-instant-fix/`, activate.
Configure API URL in Settings → AI Instant Fix.
Widget auto-injects for administrator users.

### 4. Hermes Agent

Load the `ai-instant-fix` skill. Tasks forwarded to Telegram will be auto-processed.

## Architecture

```
Browser Widget (JS) → API Server (Flask + SQLite) → Telegram → Hermes Agent → File Edit → Done
```

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | /api/tasks | Create new task |
| GET | /api/tasks?user_id=X | List tasks |
| GET | /api/tasks/:id | Get single task |
| PUT | /api/tasks/:id | Update status |
| POST | /api/tasks/:id/forward | Forward to Telegram |
| GET | /health | Health check |

## Task Status Lifecycle

```
task accepted → task on going → task completed
                               → task rejected (prompt invalid/dangerous)
```

## File Tree

```
ai-instant-fix/
├── README.md
├── GUIDE.md
├── widget/
│   ├── ai-instant-fix.js     # Vanilla JS (~180 lines, zero deps)
│   └── ai-instant-fix.css    # Styles (~120 lines)
├── server/
│   ├── app.py                # Flask API (~120 lines)
│   ├── db.py                 # SQLite helper (~70 lines)
│   ├── requirements.txt      # flask, flask-cors, requests
│   └── schema.sql            # Reference DDL
└── wp-plugin/
    ├── ai-instant-fix.php    # WordPress plugin (~80 lines)
    └── widget/
        ├── ai-instant-fix.js
        └── ai-instant-fix.css
```

## Deployment Topologies

The system is topology-agnostic: every inter-component URL is an environment
variable. Two supported layouts:

### 1. Split topology (default — works with shared hosting)

Use when the public front-end runs on **shared hosting** (e.g. Hostinger)
that cannot run long-lived daemons.

```
Browser ──▶ [FRONT-END: shared hosting]
            WP plugin / api.php  →  writes task to MySQL
                    (no outbound call needed)

[BACKEND: your own server / PC]  (must run 24/7)
  Flask API (:5556) + Hermes agent (executor)
      │
      ├─ every 30s: GET /api/poll  ──▶ pulls pending tasks from front-end DB
      ├─ executes the fix locally
      └─ pushes status back ─────────▶ front-end api.php (HMAC-signed)
```

Why it works: **all traffic is outbound from the backend.** The front-end
never needs to reach the backend directly, so the backend can sit behind
NAT / firewall / Tailscale with zero open ports.

Front-end env (shared hosting):
```
DB_HOST / DB_NAME / DB_USER / DB_PASS     # MySQL with the task table
AIF_ALLOWED_ORIGIN=https://your-site.com  # CORS
# AIF_HERMES_WEBHOOK / AIF_WEBHOOK_SECRET optional here —
# in split mode the backend pushes status via PHP_API_URL instead
```

Backend env (your server):
```
PHP_API_URL=https://your-site.com/wp-content/plugins/ai-instant-fix/api.php
JWT_SECRET=*** rand -hex 32)
WEBHOOK_SECRET=*** rand -hex 32)   # shared with whoever calls /api/webhook/task
HERMES_BIN=hermes
```
Plus a 30s cron/systemd-timer on the backend:
`curl -s --max-time 15 http://localhost:5556/api/poll`

Security note: in split mode a compromised front-end cannot touch the
backend — it has no backend secrets and no inbound path. The backend
authenticates its own writes with HMAC.

### 2. Consolidated topology (single VPS / dedicated server)

Use when you control one server end-to-end. Lower latency, no polling.

**Requirement: a VPS or dedicated server you fully control.**
Shared hosting CANNOT run this topology — it needs long-running
processes (Flask, the agent executor, optionally a local llama.cpp
server), systemd services, and persistent ports.

```
Browser ──▶ [VPS]
  WP front-end (api.php) ──direct POST──▶ Flask API (:5556)  [127.0.0.1]
  (same box)        AIF_HERMES_WEBHOOK=http://127.0.0.1:5556/api/webhook/task
                    AIF_WEBHOOK_SECRET=*** shared secret>
  Flask + Hermes agent executes locally, updates the same DB.
  Polling disabled (no ai-fix-poller timer needed).
```

Env on the single box:
```
# front-end (wp-plugin/api.php)
DB_HOST=127.0.0.1  DB_NAME=wordpress  DB_USER=wp  DB_PASS=***
AIF_HERMES_WEBHOOK=http://127.0.0.1:5556/api/webhook/task
AIF_WEBHOOK_SECRET=*** rand -hex 32)

# backend (Flask)
JWT_SECRET=*** rand -hex 32)
WEBHOOK_SECRET=*** same value as AIF_WEBHOOK_SECRET>
```

All services bind `127.0.0.1`; expose only 80/443 via your web server.
The webhook hop is a local HTTP call — instant, no timer, no polling lag.

### Choosing

| | Split | Consolidated |
|---|---|---|
| Front-end hosting | shared hosting OK | VPS/dedicated required |
| Backend must run 24/7 | yes (your machine) | yes (the VPS) |
| Task latency | up to poll interval (30s) | instant |
| Open inbound ports | none on backend | none beyond 80/443 |
| Local LLM (llama.cpp) | on backend machine | on the VPS |
| Complexity | 2 boxes, 1 secret pair | 1 box |

## Security

This project is hardened for public/production use:

- **JWT authentication** — all task endpoints require a Bearer token when `JWT_SECRET` is set
- **Webhook HMAC signatures** — PHP→Hermes calls are signed with `X-AIF-Signature` (SHA-256 HMAC over the raw body); the webhook fails closed if no secret is configured
- **Rate limiting** — per-IP sliding windows on every endpoint (login: 5/5min, create: 30/min, reads: 120/min)
- **Constant-time comparisons** — `hmac.compare_digest` / `hash_equals` for all secret checks
- **Pre-filter guard** — destructive prompts (SQL DROP/TRUNCATE, `rm -rf`, fork bombs) are blocked before reaching the agent
- **No secrets in code** — everything via environment variables (see `.env.example`)
- **No error leakage** — internal exception details are logged server-side, never returned to clients
- **Prepared statements** — all SQL uses bound parameters (MySQLi / PDO / sqlite3)

> ⚠️ Always set `JWT_SECRET` and `WEBHOOK_SECRET` in production.
> Without `JWT_SECRET`, endpoints run in open dev mode — do not expose them publicly.

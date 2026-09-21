# AI Instant Fix 🔧

Browser widget for submitting AI-powered instant fixes. Write what you want changed on any webpage — an AI agent executes it autonomously.

## How It Works

1. **Admin clicks widget** on any webpage → types a fix request (e.g. "Change the Save button to blue")
2. **Widget sends** `{prompt, url, user_id}` to the API server
3. **API server stores** the task and dispatches it to your configured **AI executor** (any CLI agent: Hermes, Claude Code, Codex, or a custom script)
4. **The executor** resolves file paths, edits files, verifies, marks the task complete via a signed callback
5. **Widget polls** the task list → shows updated status (📋 accepted → ⏳ ongoing → ✅ completed)

No framework adapters needed. The AI agent discovers files autonomously.
The control server is **executor-agnostic** — see `EXECUTOR_CMD` below.

## Quickstart

### 1. Start API Server

```bash
cd server
python3 -m venv venv && source venv/bin/activate
pip install flask flask-cors requests

# Get bot token from your Telegram bot (optional notifications)
export TELEGRAM_BOT_TOKEN=***
export TELEGRAM_CHAT_ID=your_chat_id

# Choose your AI executor (see "Choosing an AI Executor" below)
export EXECUTOR_CMD='hermes chat --query-file {prompt_file}'

python app.py  # runs on http://0.0.0.0:5555
```

### 2. Inject Widget

Add to any page (the widget injects its own styles — no CSS file needed):

```html
<script src="http://your-server:5556/widget/ai-instant-fix.js"
  data-aif-api="http://your-server:5556"
  data-aif-user-id="admin@example.com">
</script>
```

If your server requires auth (`JWT_SECRET` set), log in once and pass the token:

```html
<script src="http://your-server:5556/widget/ai-instant-fix.js"
  data-aif-api="http://your-server:5556"
  data-aif-user-id="admin"
  data-aif-token="***">
</script>
```

> Serve the widget file from your AI Fix server (Flask can serve `widget/`
> via a static route) or any CDN/static host. See `clients/vanilla/index.html`
> for a copy-paste example including dynamic (SPA-friendly) loading.

### 3. WordPress

Copy `wp-plugin/` to `/wp-content/plugins/ai-instant-fix/`, activate.
Configure API URL in Settings → AI Instant Fix.
Widget auto-injects for administrator users.

### 4. AI Executor

Configure `EXECUTOR_CMD` (or a worker queue) so the server has something
to dispatch tasks to — see "Choosing an AI Executor" below.

## Frontend Variants (Non-WordPress)

The widget is a zero-dependency vanilla JS singleton — it works on **any**
website. Ready-made integration files for popular stacks live in `clients/`:

| Stack | File | Pattern |
|---|---|---|
| Plain HTML / Shopify / Wix / Webflow | `clients/vanilla/index.html` | One `<script>` tag, or `loadAiInstantFix()` for SPAs |
| React / Next.js | `clients/react/AiInstantFix.jsx` | `<AiInstantFix api=... />` component + `useAiInstantFix()` headless hook |
| Vue 3 / Nuxt | `clients/vue/AiInstantFix.vue` | SFC wrapper component |
| Laravel | `clients/laravel/AiInstantFixController.php` | Same-origin server-side proxy (keeps AI Fix URL + token off the browser) |
| Express / Node | `clients/express/ai-fix-router.js` | `app.use('/ai-fix', aiFixRouter())` proxy |

Two integration patterns:

1. **Direct** (vanilla/React/Vue): the browser talks straight to the AI Fix
   server. Set `AIF_ALLOWED_ORIGIN` on the server to your site's origin.
   Token (if any) is visible in the page — fine for low-risk internal use;
   for anything sensitive use pattern 2.

2. **Proxy** (Laravel/Express): the browser talks to YOUR backend
   same-origin; your backend forwards to the AI Fix server with the token.
   The AI Fix server never appears in client-side code, and you can enforce
   your own auth (e.g. `auth()->id()`) before forwarding.

All variants use the same canonical REST contract:

```
POST /api/tasks   {user_id, prompt, url, page_url, parent_id?}  -> {task_id, status}
GET  /api/tasks   ?page_url=...&user_id=...&status=...&limit=50 -> {tasks, counts}
GET  /api/tasks/:id                                             -> {task, replies}
POST /api/auth/login {password}                                 -> {token}
```


## Architecture

```
Browser Widget (JS) → API Server (Flask + SQLite) → Executor (any AI CLI agent) → File Edit → Signed callback → Done
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
│   └── ai-instant-fix.js     # Universal widget (vanilla JS, self-styling)
├── clients/                  # Non-WordPress integration variants
│   ├── vanilla/index.html    # Plain HTML / Shopify / Wix / Webflow
│   ├── react/AiInstantFix.jsx    # React component + headless hook
│   ├── vue/AiInstantFix.vue      # Vue 3 SFC wrapper
│   ├── laravel/AiInstantFixController.php  # Same-origin proxy
│   └── express/ai-fix-router.js          # Same-origin proxy
├── server/
│   ├── app.py                # Flask API (REST + JWT + rate limiting)
│   ├── db.py                 # SQLite helper
│   ├── requirements.txt      # flask, flask-cors, pyjwt, requests
│   └── schema.sql            # Reference DDL
└── wp-plugin/
    ├── ai-instant-fix.php    # WordPress plugin
    └── widget/
        ├── ai-instant-fix.js
        └── ai-instant-fix.css
```

## Choosing an AI Executor

The control server does not assume any particular AI tool. When a task
arrives, it dispatches to **one** of:

### Mode 1 — Local CLI executor (`EXECUTOR_CMD`)

A shell template run on the control server. The task prompt is written
to a temp file; placeholders available: `{prompt_file}` (required),
`{task_id}`, `{user_id}`, `{url}`.

```bash
# Hermes
EXECUTOR_CMD='hermes chat --query-file {prompt_file}'

# Claude Code
EXECUTOR_CMD='claude -p "$(cat {prompt_file})"'

# Codex CLI
EXECUTOR_CMD='codex exec "$(cat {prompt_file})"'

# Your own script — receives the prompt file path as $1
EXECUTOR_CMD='/opt/my-agent/run.sh {prompt_file}'
```

The executor must finish the work and call back
`PUT /api/tasks/{id}` with an `X-AIF-Signature` HMAC header (the exact
callback command is included in the generated prompt file).

### Mode 2 — Worker queue (default when `EXECUTOR_CMD` is empty)

Tasks are POSTed to `POWER_TOOL_URL/api/tasks` with a neutral JSON
contract (`client_id`, `user_id`, `prompt`, `url`, `priority`). Any
worker system that accepts this shape can consume the queue — including
Hermes-based workers, CI runners, or custom dispatchers.

### Which mode?

| | Mode 1 (CLI) | Mode 2 (queue) |
|---|---|---|
| Setup | one env var | a worker service accepting POST |
| Concurrency | one process per task | queue-managed |
| Remote workers | no (local box only) | yes |
| Best for | single machine, quick start | multi-worker / production |

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
  Flask API (:5556) + AI executor (any CLI agent)
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
# AIF_EXECUTOR_WEBHOOK / AIF_WEBHOOK_SECRET optional here —
# in split mode the backend pushes status via PHP_API_URL instead
```

Backend env (your server):
```
PHP_API_URL=https://your-site.com/wp-content/plugins/ai-instant-fix/api.php
JWT_SECRET=*** rand -hex 32)
WEBHOOK_SECRET=*** rand -hex 32)   # shared with whoever calls /api/webhook/task
EXECUTOR_CMD='hermes chat --query-file {prompt_file}'
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
  (same box)        AIF_EXECUTOR_WEBHOOK=http://127.0.0.1:5556/api/webhook/task
                    AIF_WEBHOOK_SECRET=*** shared secret>
  Flask + AI executor runs locally, updates the same DB.
  Polling disabled (no ai-fix-poller timer needed).
```

Env on the single box:
```
# front-end (wp-plugin/api.php)
DB_HOST=127.0.0.1  DB_NAME=wordpress  DB_USER=wp  DB_PASS=***
AIF_EXECUTOR_WEBHOOK=http://127.0.0.1:5556/api/webhook/task
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
- **Webhook HMAC signatures** — PHP→executor calls are signed with `X-AIF-Signature` (SHA-256 HMAC over the raw body); the webhook fails closed if no secret is configured
- **Rate limiting** — per-IP sliding windows on every endpoint (login: 5/5min, create: 30/min, reads: 120/min)
- **Constant-time comparisons** — `hmac.compare_digest` / `hash_equals` for all secret checks
- **Pre-filter guard** — destructive prompts (SQL DROP/TRUNCATE, `rm -rf`, fork bombs) are blocked before reaching the agent
- **No secrets in code** — everything via environment variables (see `.env.example`)
- **No error leakage** — internal exception details are logged server-side, never returned to clients
- **Prepared statements** — all SQL uses bound parameters (MySQLi / PDO / sqlite3)

> ⚠️ Always set `JWT_SECRET` and `WEBHOOK_SECRET` in production.
> Without `JWT_SECRET`, endpoints run in open dev mode — do not expose them publicly.

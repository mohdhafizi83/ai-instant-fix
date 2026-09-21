# AI Instant Fix — Deployment Guide

Quick guide for deploying AI Instant Fix with any web application.

> See also `README.md` for the feature overview, "Choosing an AI Executor",
> and "Deployment Topologies". This guide covers hands-on steps.

## Part 1: Deploy the API Server

```bash
cd server
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Required secrets (generate strong values):
#   openssl rand -hex 32
export JWT_SECRET="***"        # enables JWT auth on /api/*
export WEBHOOK_SECRET="***"    # HMAC for /api/webhook/task
export ADMIN_PASSWORD="***"    # enables POST /api/auth/login

# Recommended production settings:
export AIF_ALLOWED_ORIGIN="https://your-site.com"   # comma-separated CORS allowlist
export PORT=5556

# Optional: Telegram completion notifications
export TELEGRAM_BOT_TOKEN="***"
export TELEGRAM_CHAT_ID="your_chat_id"

# Optional: AI executor (see README "Choosing an AI Executor")
export EXECUTOR_CMD='hermes chat --query-file {prompt_file}'

python app.py
```

For production: run behind a reverse proxy (nginx/Caddy) with HTTPS, and use
systemd/supervisor/Docker for process management.

### Getting a token

```bash
curl -X POST http://localhost:5556/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"password":"***"}'
# -> {"token": "***"}   (use as: Authorization: Bearer <token>)
```

If `JWT_SECRET` is unset, auth is disabled (local development only).

## Part 2: Inject the Widget

The widget (`widget/ai-instant-fix.js`) is a single zero-dependency file that
injects its own styles. The API server also serves it at `/widget/ai-instant-fix.js`.

### Method A: Script tag (any framework)

```html
<script src="https://your-api-server:5556/widget/ai-instant-fix.js"
  data-aif-api="https://your-api-server:5556"
  data-aif-user-id="{{ current_user.email }}"
  data-aif-token="{{ aif_jwt_token }}">
</script>
```

Attributes: `data-aif-api` (required), `data-aif-user-id`, `data-aif-token`
(optional JWT), `data-aif-theme` (accent color, default `#247b70`).

### Method B: Framework-specific

Ready-made wrappers live in `clients/`:

**React / Next.js** — `clients/react/AiInstantFix.jsx`
```jsx
import { AiInstantFix } from './AiInstantFix';

<AiInstantFix api="https://fix.example.com" userId={user.email} token={jwt} />
```
A headless hook (`useAiInstantFix`) is included for building custom UIs.

**Vue 3 / Nuxt** — `clients/vue/AiInstantFix.vue`
```vue
<AiInstantFix api="https://fix.example.com" user-id="admin" :token="jwt" />
```

**Laravel** — `clients/laravel/AiInstantFixController.php`
Same-origin proxy: the browser talks to YOUR domain; Laravel forwards to the
AI Fix server with the token server-side. Copy the controller, add the three
routes shown in its header comment, set `AIF_SERVER` + `AIF_TOKEN` in `.env`.

**Express / Node** — `clients/express/ai-fix-router.js`
```js
const { aiFixRouter } = require('./ai-fix-router');
app.use('/ai-fix', aiFixRouter());   // env: AIF_SERVER, AIF_TOKEN
```

**WordPress** — install the plugin from `wp-plugin/`. Auto-injects for admins.

**Rails / Django / anything else** — the script-tag method (A) works everywhere;
just render the widget URL and user id into your base layout.

## Part 3: Wire the AI Executor

The server never assumes a particular AI tool. Two modes (full details in
README "Choosing an AI Executor"):

- **Mode 1 — local CLI**: set `EXECUTOR_CMD` with a `{prompt_file}` template.
  Works with Hermes, Claude Code, Codex, or any script.
- **Mode 2 — worker queue**: leave `EXECUTOR_CMD` empty; a worker polls
  `POWER_TOOL_URL` and claims tasks via the JSON contract.

The executor processes the prompt and reports back via
`PUT /api/tasks/:id` (JWT or `X-AIF-Signature` HMAC).

## Part 4: Customize

### Theme color
```html
<script ... data-aif-theme="#ff6600"></script>
```

### Widget position
Override CSS: `#aif-root .aif-btn { bottom: 100px; left: 20px; right: auto; }`

### Reply threads
The widget automatically threads follow-up replies (`parent_id`) under
completed/rejected tasks, scoped per page (`page_url`).

## Quick Test

```bash
# Health
curl http://localhost:5556/health

# Login (if ADMIN_PASSWORD set)
TOKEN=*** -s -X POST http://localhost:5556/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"password":"***"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# Create a task
curl -X POST http://localhost:5556/api/tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"user_id":"test","prompt":"Change header color","url":"https://example.com"}'

# List tasks for a page
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:5556/api/tasks?page_url=https://example.com"

# Mark done
curl -X PUT http://localhost:5556/api/tasks/1 \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"status":"task completed"}'
```

## Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| `401 Missing or invalid Authorization header` | Server has `JWT_SECRET` set — pass a Bearer token (login first) |
| `401 Auth not configured` on login | `ADMIN_PASSWORD` or `JWT_SECRET` unset |
| `429 rate limit exceeded` | Too many requests per IP — wait for the window |
| CORS errors in browser console | Set `AIF_ALLOWED_ORIGIN` to your site's exact origin |
| Widget shows "Auth required — set a token in Settings" | Pass `data-aif-token` or use the proxy pattern (Laravel/Express) |
| Task stuck in "task accepted" | Executor not configured or failed to start — check server logs |

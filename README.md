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

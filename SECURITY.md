# Security Policy — AI Instant Fix

## Reporting a Vulnerability

Open a **private security advisory** on the GitHub repo
(mohdhafizi83/ai-instant-fix → Security → Report a vulnerability).
Do not open a public issue.

## Threat Model

Flask API server (`server/app.py`) + WordPress plugin (`wp-plugin/`).
The server dispatches user-submitted "fix this page" tasks to an AI
executor. Threats centre on unauthorised task injection, task-state
tampering, and information disclosure.

### Endpoint × Threat × Control × Test

| Endpoint | Threat | Control | Test |
|---|---|---|---|
| `POST /api/tasks` | Unauthorised task injection | JWT bearer required; **fail-closed 401** when `JWT_SECRET` unset; rate limit 30/min/IP | `test_auth.py::TestJwtFailClosed` |
| `GET /api/tasks`, `GET /api/tasks/<id>` | Task data exfiltration | JWT bearer required (fail-closed); rate limited | `test_auth.py::TestJwtFailClosed` |
| `PUT /api/tasks/<id>` | Task-state tampering | Valid JWT **or** valid HMAC `X-AIF-Signature` over raw body; 401 otherwise | `test_auth.py::TestTaskMutationAuth` |
| `POST /api/webhook/task` | Forged executor trigger | HMAC-SHA256 signature mandatory; **fails closed when `WEBHOOK_SECRET` unset**; constant-time compare | `test_auth.py::TestWebhookSignature` |
| `POST /api/auth/login` | Password brute force | 5 attempts / 5 min / IP; timing-safe compare; 501 when unconfigured | `test_auth.py::TestLogin` |
| `GET /api/poll` | Unauthenticated WP polling | JWT required (fail-closed); 501 when `PHP_API_URL` unset | `test_security.py::TestExistingBehaviour` |
| `GET /widget/<file>` | Path traversal | Filename whitelist (js/css only) | `test_security.py::TestExistingBehaviour` |
| `GET /health`, `GET /api/settings` | Info disclosure | Static minimal payload only | `test_security.py::TestExistingBehaviour` |
| All routes | Clickjacking / MIME sniffing / referrer leak | CSP (`frame-ancestors 'none'`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` | `test_security.py::TestSecurityHeaders` |
| All routes | Cross-site request from other origins | CORS restricted via `AIF_ALLOWED_ORIGIN` (env) | manual / config review |
| All routes | Crash-detail leakage | Generic JSON 404/405/413/500; tracebacks logged server-side only | `test_security.py::TestErrorHygiene` |
| All routes | Memory-exhaustion via huge bodies | `MAX_CONTENT_LENGTH` 1 MB → 413 | `test_security.py::TestBodySizeCap` |
| All routes | Credential leakage in logs | Bearer tokens / signatures never logged; werkzeug logs request line only | `test_security.py::TestLogHygiene` |
| WP `api.php` / `proxy.php` | SQL injection | `prepare()`/bound params everywhere; table names from env prefix only | code review |
| WP `proxy.php` | Open proxy / SSRF | Backend URL comes from server env `AIF_BACKEND` (admin-config), never from request; only fixed route paths are forwarded | code review |
| WP plugin | CSRF on state reads/writes | Endpoints are JSON APIs gated by rate limit + (server-side) JWT/webhook signature; admin page capability-gated (`manage_options`) | code review |

## JWT Fail-Closed + DEV_ALLOW_ANON

`jwt_required` **fails closed**: if `JWT_SECRET` is not set, every
JWT-protected route returns `401 {"error":"authentication not configured"}`.
This is deliberate — a forgotten secret must never mean "open".

The only escape hatch is `DEV_ALLOW_ANON=1`, which re-enables anonymous
local development. It is **dev-only**: the startup banner prints a loud
warning while it is active, and any value other than exactly `1` is
ignored. Never set it in production.

## Webhook Signatures

`POST /api/webhook/task` and webhook-authenticated `PUT /api/tasks/<id>`
require `X-AIF-Signature: hex(HMAC-SHA256(WEBHOOK_SECRET, raw_body))`.
Signatures are compared in constant time. If `WEBHOOK_SECRET` is unset
the webhook is **disabled** (401 for every request) — signatures are
mandatory, never optional.

## Out of Scope

- Attacks from an already-authenticated JWT holder (trusted operator).
- Compromise of the host OS, the reverse proxy, or the WordPress admin.
- The AI executor's own sandboxing (executor is operator-configured).
- Prompt-injection effects *inside* the downstream AI agent — the
  pre-filter is a defence-in-depth speed bump, not a boundary.
- `clients/` example code (illustrative snippets, not deployed as-is).
- WordPress core/theme/plugin vulnerabilities.

## Hardening Checklist for Deployments

- Set `JWT_SECRET` (≥32 random bytes) and `WEBHOOK_SECRET` (`openssl rand -hex 32`).
- Set `AIF_ALLOWED_ORIGIN` to your exact front-end origin(s).
- Serve over TLS via a reverse proxy; do not expose Flask directly.
- Keep `DEV_ALLOW_ANON` unset in production.
- CI (`.github/workflows/ci.yml`) runs pytest, bandit, pip-audit, gitleaks on every push/PR.

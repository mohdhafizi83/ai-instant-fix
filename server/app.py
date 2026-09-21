"""
AI Instant Fix — API Server v2.0
- Pluggable AI executor dispatch (any CLI agent)
- JWT authentication (optional)
- Telegram completion notification
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import db
import os
import subprocess
import shlex
import time
import requests as http_requests

app = Flask(__name__)
# CORS: restrict to your front-end origin(s) via env (comma-separated).
# Falls back to '*' only when unset (local development).
_allowed_origins = [o.strip() for o in os.environ.get('AIF_ALLOWED_ORIGIN', '').split(',') if o.strip()]
if _allowed_origins:
    CORS(app, resources={r'/api/*': {'origins': _allowed_origins}})
else:
    CORS(app)  # dev default — set AIF_ALLOWED_ORIGIN in production


# ── Simple in-memory rate limiter (per-IP, sliding window) ──
_rate_buckets = {}

def rate_limit(limit, window_sec):
    """Decorator: allow at most `limit` requests per `window_sec` per client IP."""
    import functools
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (fn.__name__, request.remote_addr)
            now = time.time()
            hits = [t for t in _rate_buckets.get(key, []) if now - t < window_sec]
            if len(hits) >= limit:
                return jsonify({'error': 'rate limit exceeded'}), 429
            hits.append(now)
            _rate_buckets[key] = hits
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ── Webhook signature verification (HMAC-SHA256 over raw body) ──
def verify_webhook_signature():
    """Return True if WEBHOOK_SECRET is unset (dev mode) or signature is valid."""
    if not WEBHOOK_SECRET:
        return False  # fail closed: webhook disabled unless secret configured
    import hmac, hashlib
    signature = request.headers.get('X-AIF-Signature', '')
    if not signature:
        return False
    raw = request.get_data()
    expected = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)

# ── Config (env vars) ─────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID', '')
# Executor: any AI CLI agent (Hermes, Claude Code, Codex, custom script).
# EXECUTOR_CMD is a shell template; {prompt_file} is replaced with a
# temp file containing the task prompt. Empty = dispatch to worker queue.
EXECUTOR_CMD     = os.environ.get('EXECUTOR_CMD', '')
EXECUTOR_PROFILE = os.environ.get('EXECUTOR_PROFILE',
                       os.environ.get('HERMES_PROFILE', 'default'))
JWT_SECRET         = os.environ.get('JWT_SECRET', '')
# Shared secret used to sign internal webhook calls (PHP plugin -> this server).
WEBHOOK_SECRET     = os.environ.get('WEBHOOK_SECRET', '')
API_URL            = os.environ.get('API_URL',
                       f"http://localhost:{os.environ.get('PORT', '5556')}")
PHP_API_URL        = os.environ.get('PHP_API_URL', '')
POWER_TOOL_URL     = os.environ.get('POWER_TOOL_URL', 'http://localhost:5557')
POWER_TOOL_CLIENT  = os.environ.get('POWER_TOOL_CLIENT_ID', None)  # None → auto-resolve from domain

# ── Client resolution cache (domain → client_id) ──────────
# Avoids hammering the Power Tool API for every task
_client_cache = {}
_client_cache_ttl = 300  # 5 minutes

def resolve_client_by_domain(url):
    """Resolve client_id from URL domain via Power Tool API.
    
    Extracts domain from URL, queries Power Tool's client list,
    and matches by website_url domain. Cached for 5 minutes.
    Falls back to POWER_TOOL_CLIENT_ID env var if resolution fails.
    """
    from urllib.parse import urlparse
    import time as _time
    
    if not url:
        return _fallback_client_id()
    
    try:
        domain = urlparse(url).netloc.lower()
        # Strip www prefix for matching
        if domain.startswith('www.'):
            domain = domain[4:]
    except Exception:
        return _fallback_client_id()
    
    if not domain:
        return _fallback_client_id()
    
    # Check cache
    now = _time.time()
    if domain in _client_cache:
        cached_id, cached_at = _client_cache[domain]
        if now - cached_at < _client_cache_ttl:
            return cached_id
    
    # Query Power Tool API for all clients
    try:
        resp = http_requests.get(f'{POWER_TOOL_URL}/api/clients', timeout=5)
        if resp.status_code == 200:
            clients = resp.json().get('clients', [])
            for c in clients:
                client_domain = None
                website = (c.get('website_url') or '').strip()
                if website:
                    try:
                        client_domain = urlparse(website).netloc.lower()
                        if client_domain.startswith('www.'):
                            client_domain = client_domain[4:]
                    except Exception:
                        pass
                
                # Match by domain
                if client_domain and client_domain == domain:
                    cid = c['id']
                    _client_cache[domain] = (cid, now)
                    print(f'[AIF] Resolved {domain} → client #{cid} ({c["name"]})', flush=True)
                    return cid
                
                # Also match by name pattern (e.g., client "acme" → acme.example.com)
                client_name = (c.get('name') or '').lower()
                if client_name and client_name in domain:
                    cid = c['id']
                    _client_cache[domain] = (cid, now)
                    print(f'[AIF] Resolved {domain} → client #{cid} ({c["name"]}) by name match', flush=True)
                    return cid
    except Exception as e:
        print(f'[AIF] Client resolution query failed: {e}', flush=True)
    
    return _fallback_client_id()


def _fallback_client_id():
    """Return fallback client ID from env var or None."""
    fb = os.environ.get('POWER_TOOL_CLIENT_ID')
    if fb is not None:
        try:
            return int(fb)
        except (ValueError, TypeError):
            pass
    return None


def _validate_client_id(client_id):
    """Quick validation: client_id must be a positive integer."""
    if client_id is None:
        return False
    try:
        cid = int(client_id)
        return cid > 0
    except (ValueError, TypeError):
        return False


# ── Power Tool Sync ─────────────────────────────────────────

def sync_to_power_tool(prompt, url, status='pending', client_id=None):
    """Sync task to Power Tool DB for tracking. Fire-and-forget.
    
    Args:
        prompt: Task description
        url: Page URL being fixed
        status: Task status ('pending' | 'processing' | 'completed')
        client_id: Explicit client ID. If None, auto-resolve from URL domain.
                   Falls back to POWER_TOOL_CLIENT_ID env var, then skips.
    
    Returns task_id or None.
    """
    # Resolve client ID
    resolved_id = client_id
    if resolved_id is None and url:
        resolved_id = resolve_client_by_domain(url)
    if resolved_id is None:
        resolved_id = _fallback_client_id()
    
    if not _validate_client_id(resolved_id):
        print(f'[AIF] Power Tool sync SKIPPED — no client resolved for URL: {url}', flush=True)
        return None
    
    resolved_id = int(resolved_id)
    
    try:
        resp = http_requests.post(f'{POWER_TOOL_URL}/api/tasks', json={
            'client_id': resolved_id,
            'user_id': 'widget:ai-instant-fix',
            'prompt': prompt,
            'url': url,
            'priority': 1
        }, timeout=5)
        if resp.status_code == 201:
            data = resp.json()
            task_id = data.get('task_id')
            # Update status if needed
            if status != 'pending' and task_id:
                http_requests.put(f'{POWER_TOOL_URL}/api/tasks/{task_id}/status',
                    json={'status': status}, timeout=5)
            return task_id
    except Exception as e:
        print(f'[AIF] Power Tool sync failed: {e}', flush=True)
    return None

# ── JWT Helpers ───────────────────────────────────────

def _jwt_decode(token):
    """Decode JWT or raise. Returns payload dict."""
    import jwt
    return jwt.decode(token, JWT_SECRET, algorithms=['HS256'])


def jwt_required(f):
    """Decorator: require valid JWT Bearer token when JWT_SECRET is set."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not JWT_SECRET:
            return f(*args, **kwargs)  # auth disabled
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'error': 'Missing or invalid Authorization header'}), 401
        try:
            _jwt_decode(auth.split(' ', 1)[1])
        except Exception:
            return jsonify({'error': 'Invalid or expired token'}), 401
        return f(*args, **kwargs)

    return decorated


def _auth_allows_mutation():
    """PUT /api/tasks/<id> may be called by a JWT holder OR a signed webhook.

    The AI executor (running on this host) and the PHP plugin both report
    task status back. Either a valid Bearer JWT or a valid X-AIF-Signature
    over the raw request body is accepted.
    """
    auth = request.headers.get('Authorization', '')
    if JWT_SECRET and auth.startswith('Bearer '):
        try:
            _jwt_decode(auth.split(' ', 1)[1])
            return True
        except Exception:
            pass
    return verify_webhook_signature()


# ── Telegram notification ─────────────────────────────

def notify_telegram(text):
    """Send a plain-text Telegram message. No-op if not configured."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print('[AIF] Telegram not configured, skipping notification', flush=True)
        return False
    import requests
    try:
        resp = requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': text},
            timeout=10,
        )
        ok = resp.status_code == 200
        if not ok:
            print(f'[AIF] Telegram error {resp.status_code}: {resp.text[:200]}', flush=True)
        return ok
    except Exception as e:
        print(f'[AIF] Telegram exception: {e}', flush=True)
        return False


# ── Pre-Filter (liberal — only block obvious bad tasks) ─

import re as _re

# Patterns that are clearly destructive — liberal mode: only the most explicit
_DESTRUCTIVE_PATTERNS = [
    # SQL — must match full statement pattern, not innocent mentions
    (r'\bDROP\s+TABLE\b',        'SQL DROP TABLE'),
    (r'\bDROP\s+DATABASE\b',      'SQL DROP DATABASE'),
    (r'\bTRUNCATE\s+TABLE\b',     'SQL TRUNCATE TABLE'),
    (r'\bDELETE\s+FROM\s+\w+',    'SQL DELETE FROM (no WHERE visible)'),
    (r'\bDROP\s+USER\b',          'SQL DROP USER'),
    # Shell — explicit destructive commands
    (r'\brm\s+-rf\b',             'shell rm -rf'),
    (r'\bsudo\s+rm\b',            'shell sudo rm'),
    (r'\bchmod\s+777\b',          'shell chmod 777'),
    (r':\(\)\s*\{\s*:\|:&\s*\}\s*;:',  'fork bomb'),
]

# Minimum prompt length after stripping — anything shorter is too vague
_MIN_PROMPT_LENGTH = 5


def prefilter_task(prompt, url=''):
    """Lightweight pre-filter to catch obviously bad tasks before dispatching to the executor.

    Liberal mode: only blocks the most egregious cases. The LLM-level
    Safety Rules in the ai-instant-fix skill handle nuanced decisions.

    Returns:
        (is_safe: bool, block_reason: str | None, suggested_status: str | None)
    """
    p = (prompt or '').strip()

    # 1. Unclear / too short
    if len(p) < _MIN_PROMPT_LENGTH:
        return False, f'Prompt too short ({len(p)} chars)', 'prompt_ask'

    # 2. Destructive patterns (case-insensitive via re.IGNORECASE flag)
    for pattern, label in _DESTRUCTIVE_PATTERNS:
        if _re.search(pattern, p, _re.IGNORECASE):
            return False, f'Destructive pattern detected: {label}', 'dangerous_stop'

    # 3. Liberal mode: skip complexity check — let LLM decide

    return True, None, None


# ── Executor dispatch (pluggable AI agent) ────────────

def build_task_prompt(task_id, user_id, prompt, url, path_files=None):
    """Build a self-contained executor prompt for one task."""
    parts = [
        f"AI Instant Fix task #{task_id}",
        "",
        f"User: {user_id}",
        f"Page URL: {url}",
        f"Fix requested: {prompt}",
        "",
        f"IMPORTANT: Restrict changes to this page ({url}) only unless stated otherwise.",
        "Do NOT modify files or functions unrelated to that page.",
    ]
    if path_files:
        parts.append(f"Relevant files (hint): {path_files}")

    parts += [
        "",
        "STEPS:",
        f"1. Read and understand the fix: {prompt}",
        f"2. Find the relevant source files for URL {url}",
        "3. Make the changes using the patch tool",
        "4. Run syntax checks and verify",
        f"5. When COMPLETE, call this EXACT command to mark the task complete",
        f"   (signature is computed from the WEBHOOK_SECRET env var — never print it):",
        f"   BODY='{{\"status\":\"task completed\"}}'; "
        f"SIG=$(printf '%s' \"$BODY\" | openssl dgst -sha256 -hmac \"$WEBHOOK_SECRET\" -hex | awk '{{print $2}}'); "
        f"curl -s -X PUT http://localhost:5556/api/tasks/{task_id} "
        f"-H 'Content-Type: application/json' -H \"X-AIF-Signature: $SIG\" -d \"$BODY\"",
        "",
        "IMPORTANT: Do NOT mark the task complete until you have ACTUALLY made and verified the changes.",
    ]
    return "\n".join(parts)


def spawn_executor(task_id, user_id, prompt, url, path_files=None):
    """Dispatch a task to the configured AI executor.

    Two executor modes:
    1. EXECUTOR_CMD set  → run locally as a shell template.
       Placeholders: {prompt_file} (required), {task_id}, {user_id}, {url}.
       Examples:
         EXECUTOR_CMD="hermes chat --profile $EXECUTOR_PROFILE --query-file {prompt_file}"
         EXECUTOR_CMD="claude -p \"$(cat {prompt_file})\""
         EXECUTOR_CMD="/opt/my-agent/run.sh {prompt_file}"
    2. EXECUTOR_CMD empty → dispatch to a worker queue via POWER_TOOL_URL
       (any worker system that accepts POST /api/tasks).
    """
    task_prompt = build_task_prompt(task_id, user_id, prompt, url, path_files)

    if EXECUTOR_CMD:
        # Mode 1: local CLI executor via shell template.
        import subprocess, tempfile
        fd, prompt_file = tempfile.mkstemp(prefix=f"aif-{task_id}-", suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write(task_prompt)
        try:
            cmd = EXECUTOR_CMD.format(
                prompt_file=prompt_file, task_id=task_id,
                user_id=user_id, url=url,
            )
        except (KeyError, IndexError) as e:
            os.unlink(prompt_file)
            print(f"[AIF] Invalid EXECUTOR_CMD template ({e})", flush=True)
            return False
        print(f"[AIF] Executing task #{task_id} via EXECUTOR_CMD (profile={EXECUTOR_PROFILE})", flush=True)
        try:
            subprocess.Popen(
                cmd, shell=True,
                stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT,
                env={**os.environ, "AIF_TASK_ID": str(task_id)},
            )
            return True
        except Exception as e:
            print(f"[AIF] EXECUTOR_CMD failed to start for task #{task_id}: {e}", flush=True)
            return False

    # Mode 2: worker queue dispatch (neutral HTTP contract).
    power_tool_url = os.environ.get('POWER_TOOL_URL', 'http://localhost:5557')
    payload = {
        'client_id': int(os.environ.get('POWER_TOOL_CLIENT_ID', '1')),
        'user_id': f'aif:{user_id}',
        'prompt': task_prompt,
        'url': url,
        'path_files': ';'.join(path_files) if path_files else None,
        'priority': 2,  # Urgent
    }

    print(f'[AIF] Dispatching task #{task_id} to Worker Queue (profile={EXECUTOR_PROFILE})', flush=True)
    try:
        import requests as req
        resp = req.post(f'{power_tool_url}/api/tasks', json=payload, timeout=10)
        if resp.status_code == 201:
            data = resp.json()
            print(f'[AIF] Task #{task_id} queued → worker task #{data.get("task_id")} ({data.get("status")})', flush=True)
            return True
        else:
            print(f'[AIF] Worker queue returned {resp.status_code}: {resp.text[:200]}', flush=True)
            return False
    except Exception as e:
        print(f'[AIF] Failed to dispatch task #{task_id} to Worker Queue: {e}', flush=True)
        return False


# ═══════════════════════════════════════════════════════
#  API Routes
# ═══════════════════════════════════════════════════════

# ── Shared task processing (used by ALL task entry points) ──

def _process_task_request(user_id, prompt, url, path_files=None, remote_id=None,
                         page_url=None, parent_id=None):
    """Core task processing: pre-filter → DB → sync → notify → spawn.
    
    This is the SINGLE processing path for all task entry points.
    Returns (response_dict, http_status_code).
    """
    if not prompt or not url:
        return {'error': 'prompt and url are required'}, 400

    # ── Pre-filter: block obviously bad tasks before any processing ──
    is_safe, block_reason, suggested_status = prefilter_task(prompt, url)
    if not is_safe:
        print(f'[AIF] PRE-FILTER BLOCKED: {block_reason} | prompt={prompt[:80]}', flush=True)
        task_id = db.create_task(user_id, prompt, url, path_files,
                                page_url=page_url, parent_id=parent_id)
        db.update_task_status(task_id, suggested_status)
        notify_telegram(
            f"\U0001F6D1 *AI Fix #{task_id} BLOCKED*\n"
            f"\U0001F4DD {prompt}\n"
            f"\U0001F310 {url}\n"
            f"\u26A0\uFE0F {block_reason}\n"
            f"\U0001F4CD Status: {suggested_status}"
        )
        return {
            'task_id': task_id,
            'status': suggested_status,
            'blocked': True,
            'reason': block_reason,
        }, 201

    task_id = db.create_task(user_id, prompt, url, path_files,
                            page_url=page_url, parent_id=parent_id)

    # Sync to Power Tool DB for tracking
    sync_to_power_tool(prompt, url, status='processing')

    # Notify: task received
    notify_telegram(
        f"\U0001F4CB *AI Fix #{task_id} received*\n"
        f"\U0001f4dd {prompt}\n"
        f"\U0001f310 {url}\n"
        f"\U0001f464 {user_id}"
    )

    # Fire-and-forget: dispatch to the configured AI executor
    ok = spawn_executor(task_id, user_id, prompt, url, path_files)
    if ok:
        db.update_task_status(task_id, 'task on going')
        return {'task_id': task_id, 'status': 'task on going'}, 201
    else:
        return {
            'task_id': task_id,
            'status': 'task accepted',
            'warning': 'Executor failed to start; task saved',
        }, 201


# ── PATH A (primary): Widget → direct POST ────────────
@app.route('/api/tasks', methods=['POST'])
@jwt_required
@rate_limit(30, 60)
def create():
    """Widget sends task directly to AI Fix API. JWT-protected."""
    data = request.get_json(force=True)
    user_id   = data.get('user_id', 'anonymous')
    prompt    = (data.get('prompt') or '').strip()
    url       = (data.get('url') or '').strip()
    path_files = data.get('path_files')
    page_url  = (data.get('page_url') or url).strip()
    parent_id = data.get('parent_id')

    result, code = _process_task_request(user_id, prompt, url, path_files,
                                        page_url=page_url, parent_id=parent_id)
    return jsonify(result), code


# ── List tasks ─────────────────────────────────────────
@app.route('/api/tasks', methods=['GET'])
@jwt_required
@rate_limit(120, 60)
def list_tasks():
    user_id = request.args.get('user_id')
    status  = request.args.get('status')
    page_url = request.args.get('page_url')
    try:
        limit = min(int(request.args.get('limit', 50)), 200)
    except ValueError:
        return jsonify({'error': 'invalid limit'}), 400
    tasks   = db.get_tasks(user_id=user_id, status=status, page_url=page_url, limit=limit)
    counts  = db.count_tasks(user_id=user_id, page_url=page_url)
    return jsonify({'tasks': tasks, 'counts': counts})


# ── Get single task ────────────────────────────────────
@app.route('/api/tasks/<int:task_id>', methods=['GET'])
@jwt_required
@rate_limit(120, 60)
def get(task_id):
    task = db.get_task(task_id)
    if not task:
        return jsonify({'error': 'task not found'}), 404
    return jsonify(task)


# ── Update task status (+ Telegram notify on complete) ─
@app.route('/api/tasks/<int:task_id>', methods=['PUT'])
@rate_limit(60, 60)
def update(task_id):
    # Only a valid JWT or a valid webhook signature may mutate task state.
    if not _auth_allows_mutation():
        return jsonify({'error': 'authentication required'}), 401
    data   = request.get_json(force=True)
    status = data.get('status', '')

    try:
        db.update_task_status(task_id, status)
    except ValueError:
        return jsonify({'error': 'invalid status'}), 400

    task = db.get_task(task_id)

    # ── Telegram notification + WordPress sync for terminal statuses ──
    _notify_and_sync(task_id, status, task)

    return jsonify({'task_id': task_id, 'status': status})


# ── Terminal-status notification + WordPress sync ─────

_TERMINAL_NOTIFY = {
    'task completed':  ('✅', '*AI Fix #{tid} completed*'),
    'dangerous_stop':  ('🛑', '*AI Fix #{tid} BLOCKED (Dangerous)*'),
    'complex_send':    ('⚠️', '*AI Fix #{tid} BLOCKED (Too Complex)*'),
    'prompt_ask':      ('❓', '*AI Fix #{tid} BLOCKED (Unclear)*'),
    'task rejected':   ('❌', '*AI Fix #{tid} REJECTED*'),
}


def _notify_and_sync(task_id, status, task):
    """Send Telegram notification + sync to WordPress for terminal statuses."""
    if status not in _TERMINAL_NOTIFY:
        return  # non-terminal statuses (task accepted, task on going) — silent

    if not task:
        return  # no task data to work with

    emoji, header = _TERMINAL_NOTIFY[status]
    header = header.format(tid=task_id)

    # Telegram notification
    lines = [
        f"{emoji} {header}",
        f"📝 {task['prompt']}",
        f"🌐 {task['url']}",
        f"👤 {task['user_id']}",
    ]
    if status == 'dangerous_stop':
        lines.append("⚠️ Dangerous action detected — not executed")
    elif status == 'complex_send':
        lines.append("⚠️ Too complex for AI — human needed")
    elif status == 'prompt_ask':
        lines.append("❓ Unclear instruction — clarification needed")

    notify_telegram("\n".join(lines))

    # WordPress sync via subprocess curl (reliable)
    _sync_to_wordpress(task_id, status)


def _sync_to_wordpress(task_id, status):
    """Sync task status to WordPress DB via subprocess curl."""
    wp_url = PHP_API_URL.replace('/wp-content/plugins/ai-instant-fix/api.php', '')
    try:
        import subprocess as sp
        import json
        result = sp.run([
            'curl', '-s', '-X', 'POST',
            f'{wp_url}?aif_action=update&id={task_id}',
            '-H', 'Content-Type: application/json',
            '-d', json.dumps({'status': status}),
            '--max-time', '10',
        ], capture_output=True, text=True, timeout=15)
        with open('/tmp/aif-sync.log', 'a') as f:
            f.write(f'[AIF] Sync #{task_id} ({status}): {result.stdout.strip()[:100]}\n')
    except Exception as e:
        with open('/tmp/aif-sync.log', 'a') as f:
            f.write(f'[AIF] Sync #{task_id} ({status}) FAILED: {e}\n')


# ── Settings (for widget auto-config) ──────────────────
@app.route('/api/settings', methods=['GET'])
def settings():
    return jsonify({
        'api_url':        API_URL,
        'executor_profile': EXECUTOR_PROFILE,
        'auth_required':  bool(JWT_SECRET),
    })


# ── Login / token issue ────────────────────────────────
@app.route('/api/auth/login', methods=['POST'])
@rate_limit(5, 300)  # 5 attempts per 5 minutes per IP — brute-force protection
def login():
    """Simple password-based token issue. Set ADMIN_PASSWORD env var."""
    admin_pw = os.environ.get('ADMIN_PASSWORD', '')
    if not admin_pw or not JWT_SECRET:
        return jsonify({'error': 'Auth not configured'}), 501

    data = request.get_json(force=True)
    import hmac
    if not hmac.compare_digest(str(data.get('password', '')), admin_pw):
        return jsonify({'error': 'Invalid password'}), 401

    import jwt as _jwt
    token = _jwt.encode(
        {'sub': 'admin', 'iat': int(time.time())},
        JWT_SECRET,
        algorithm='HS256',
    )
    return jsonify({'token': token})


# ── Health check ───────────────────────────────────────
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})


# ── Serve the universal widget (so frontends can load it from here) ──
_WIDGET_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'widget'))

@app.route('/widget/<path:filename>', methods=['GET'])
def serve_widget(filename):
    # Whitelist: only the widget JS/CSS, nothing else.
    if filename not in ('ai-instant-fix.js', 'ai-instant-fix.css'):
        return jsonify({'error': 'not found'}), 404
    from flask import send_from_directory
    return send_from_directory(_WIDGET_DIR, filename,
                              mimetype='application/javascript' if filename.endswith('.js') else 'text/css',
                              max_age=3600)


# ── PATH C (webhook): PHP plugin → direct trigger ─────
@app.route('/api/webhook/task', methods=['POST'])
@rate_limit(30, 60)
def webhook_task():
    """Called by PHP plugin to trigger the AI executor on this server.

    Requires a valid HMAC signature (X-AIF-Signature) computed over the
    raw request body with WEBHOOK_SECRET. Fails closed if WEBHOOK_SECRET
    is not configured.
    """
    if not verify_webhook_signature():
        return jsonify({'error': 'invalid or missing webhook signature'}), 401
    data = request.get_json(force=True)
    user_id    = data.get('user_id', 'anonymous')
    prompt     = (data.get('prompt') or '').strip()
    url        = (data.get('url') or '').strip()
    path_files = data.get('path_files')

    result, code = _process_task_request(user_id, prompt, url, path_files)
    return jsonify(result), code


# ── Poll Hostinger for pending tasks ─────────────────
@app.route('/api/poll', methods=['GET'])
@jwt_required
@rate_limit(60, 60)
def poll_tasks():
    """Poll WordPress plugin API on Hostinger for pending tasks."""
    import requests as req
    if not PHP_API_URL:
        return jsonify({'error': 'PHP_API_URL not configured'}), 501
    # Use WordPress init hook endpoint (not api.php — blocked by Hostinger)
    wp_base = PHP_API_URL.replace('/wp-content/plugins/ai-instant-fix/api.php', '')
    try:
        # Get tasks with status 'task accepted'
        resp = req.get(f'{wp_base}/?aif_action=list&status=task+accepted&limit=5', timeout=10)
        data = resp.json()
        tasks = data.get('tasks', [])
        results = []
        for t in tasks:
            tid = t['id']
            uid = t.get('user_id', '')
            prompt = t.get('prompt', '')
            url   = t.get('url', '')

            # ── Pre-filter: block obviously bad tasks before processing ──
            is_safe, block_reason, suggested_status = prefilter_task(prompt, url)
            if not is_safe:
                print(f'[AIF] PRE-FILTER BLOCKED: {block_reason} | prompt={prompt[:80]}', flush=True)
                # Mark WordPress with blocked status
                req.post(f'{wp_base}/?aif_action=update&id={tid}',
                         json={'status': suggested_status}, timeout=10)
                # Insert into local DB with blocked status
                try:
                    import sqlite3
                    conn = sqlite3.connect(db.DB_PATH)
                    conn.execute(
                        "INSERT OR IGNORE INTO ai_instant_task (id, user_id, prompt, url, status) VALUES (?, ?, ?, ?, ?)",
                        (int(tid), uid, prompt, url, 'task accepted')
                    )
                    conn.commit()
                    conn.close()
                    db.update_task_status(int(tid), suggested_status)
                except:
                    pass
                # Notify Telegram — task blocked
                notify_telegram(
                    f"\U0001F6D1 *AI Fix #{tid} BLOCKED*\n"
                    f"\U0001F4DD {prompt}\n"
                    f"\U0001F310 {url}\n"
                    f"\u26A0\uFE0F {block_reason}\n"
                    f"\U0001F4CD Status: {suggested_status}"
                )
                results.append({'task_id': tid, 'blocked': True, 'reason': block_reason})
                continue

            # ── Safe task — proceed normally ──
            # Mark as task on going (WordPress)
            req.post(f'{wp_base}/?aif_action=update&id={tid}',
                     json={'status': 'task on going'}, timeout=10)
            # Create in local DB so PUT completion handler can find it
            try:
                import sqlite3
                conn = sqlite3.connect(db.DB_PATH)
                conn.execute(
                    "INSERT OR IGNORE INTO ai_instant_task (id, user_id, prompt, url, status) VALUES (?, ?, ?, ?, ?)",
                    (int(tid), uid, prompt, url, 'task accepted')
                )
                conn.commit()
                conn.close()
                db.update_task_status(int(tid), 'task on going')
            except:
                pass  # may already exist
            # Notify Telegram — task received
            notify_telegram(
                f"\U0001F4CB *AI Fix #{tid} received*\n"
                f"\U0001f4dd {prompt}\n"
                f"\U0001f310 {url}\n"
                f"\U0001f464 {uid}"
            )
            # Dispatch to executor
            ok = spawn_executor(tid, uid, prompt, url, t.get('path_files'))
            results.append({'task_id': tid, 'executor_dispatched': ok})
        return jsonify({'polled': len(results), 'results': results})
    except Exception as e:
        print(f'[AIF] Poll failed: {e}', flush=True)
        return jsonify({'error': 'upstream poll failed'}), 502


# ═══════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════
if __name__ == '__main__':
    db.init_db()
    port = int(os.environ.get('PORT', 5556))
    print(f'AI Instant Fix API v2.0  http://0.0.0.0:{port}')
    print(f'  Executor       : {EXECUTOR_CMD or "worker queue"} (profile={EXECUTOR_PROFILE})')
    print(f'  JWT auth       : {"ON" if JWT_SECRET else "OFF"}')
    print(f'  Telegram notify: {"ON" if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID else "OFF"}')
    app.run(host='0.0.0.0', port=port, debug=False)

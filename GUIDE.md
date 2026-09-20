# AI Instant Fix — Deployment Guide

Quick guide for deploying AI Instant Fix on any web application.

## Part 1: Deploy API Server

```bash
cd server
python3 -m venv venv && source venv/bin/activate
pip install flask flask-cors requests

# Set env vars
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"
export PORT=5555

python app.py
```

For production: use systemd, supervisor, or Docker. Add nginx reverse proxy + HTTPS.

## Part 2: Inject Widget

The widget is a single JS file with zero dependencies. Inject it via:

### Method A: Script tag (any framework)

```html
<script src="/path/to/ai-instant-fix.js"
  data-aif-api="https://your-api-server:5555"
  data-aif-user-id="{{ current_user.email }}">
</script>
```

### Method B: Framework-specific

**WordPress**: Install the plugin from `wp-plugin/`. Auto-injects for admins.

**Laravel**: Add to base layout:
```blade
@if(auth()->check() && auth()->user()->isAdmin())
<script src="{{ asset('js/ai-instant-fix.js') }}"
  data-aif-api="{{ env('AIF_API_URL') }}"
  data-aif-user-id="{{ auth()->user()->email }}">
</script>
@endif
```

**Rails**: Add to `app/views/layouts/application.html.erb`:
```erb
<% if current_user&.admin? %>
  <%= javascript_include_tag 'ai-instant-fix.js',
        'data-aif-api': ENV['AIF_API_URL'],
        'data-aif-user-id': current_user.email %>
<% end %>
```

**Django**: Add to base template:
```html
{% if user.is_staff %}
<script src="{% static 'js/ai-instant-fix.js' %}"
  data-aif-api="{{ AIF_API_URL }}"
  data-aif-user-id="{{ user.username }}">
</script>
{% endif %}
```

**React/Next.js**: Create a component:
```jsx
'use client';
import Script from 'next/script';

export function AiInstantFix({ userId, apiBase }) {
  return (
    <Script src="/js/ai-instant-fix.js"
      data-aif-api={apiBase}
      data-aif-user-id={userId}
      strategy="afterInteractive"
    />
  );
}
```

## Part 3: Configure Hermes Agent

Load the `ai-instant-fix` skill. Set environment variable:
```bash
export API_SERVER=http://your-api:5555
```

Hermes will:
1. Receive task via Telegram
2. Resolve file paths using `search_files` and `grep`
3. Edit files via the terminal tool
4. Update task status back to API
5. Reply on Telegram

The agent resolves files autonomously — no per-framework adapter code needed.

## Part 4: Customize

### Theme color
```html
<script ... data-aif-theme="#ff6600"></script>
```

### Widget position
Override CSS: `#aif-root { bottom: 100px; left: 20px; right: auto; }`

### Authentication
For production, add a JWT/HMAC middleware to the Flask API:
```python
# In app.py, add a before_request hook
@app.before_request
def check_auth():
    if request.path == '/health':
        return
    token = request.headers.get('X-AIF-Token')
    if not verify_token(token):
        return jsonify({'error': 'unauthorized'}), 401
```

## Quick Test

```bash
# Start API
cd server && python app.py &

# Create task
curl -X POST http://localhost:5555/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","prompt":"Change header color","url":"https://example.com"}'

# List tasks
curl http://localhost:5555/api/tasks?user_id=test

# Forward to Telegram
curl -X POST http://localhost:5555/api/tasks/1/forward

# Mark done
curl -X PUT http://localhost:5555/api/tasks/1 \
  -H "Content-Type: application/json" \
  -d '{"status":"task completed"}'
```

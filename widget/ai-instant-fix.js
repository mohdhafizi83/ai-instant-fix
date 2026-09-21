/**
 * AI Instant Fix — Universal Browser Widget v7.0
 *
 * Zero-dependency floating widget. Works on ANY website (WordPress, Shopify,
 * Wix, plain HTML, React, Vue, ...) via a single <script> tag.
 *
 * Talks to the canonical REST backend (server/app.py):
 *   POST /api/tasks        {user_id, prompt, url, page_url, parent_id}
 *   GET  /api/tasks?page_url=...&limit=50
 *   GET  /api/tasks/:id
 *   POST /api/auth/login   {password} -> {token}   (optional)
 *
 * Configure via data attributes on the <script> tag:
 *   data-aif-api     Base URL of the AI Fix server (required)
 *                    e.g. https://fix.example.com  (no trailing slash)
 *   data-aif-user-id User identifier (default: anonymous)
 *   data-aif-token   JWT bearer token (optional; obtain via /api/auth/login)
 *   data-aif-theme   Accent color (default: #247b70)
 *
 * Example:
 *   <script src="https://fix.example.com/widget/ai-instant-fix.js"
 *           data-aif-api="https://fix.example.com"
 *           data-aif-user-id="admin"></script>
 */
(function () {
  'use strict';

  if (window.__AIF_LOADED__) return;
  window.__AIF_LOADED__ = true;

  var S = document.currentScript || {};
  var C = {
    api:   (S.dataset && S.dataset.aifApi) || '',
    uid:   (S.dataset && S.dataset.aifUserId) || 'anonymous',
    token: (S.dataset && S.dataset.aifToken) || '',
    theme: (S.dataset && S.dataset.aifTheme) || '#247b70',
  };
  if (!C.api) {
    console.warn('AI Instant Fix: missing data-aif-api attribute. Widget disabled.');
    return;
  }
  C.api = C.api.replace(/\/+$/, '');

  var tasks = [];
  var replyTo = null;
  var open = false;
  var pollTimer = null;

  var STATUS_EMOJI = {
    'task accepted': '\uD83D\uDCCB', 'task on going': '\u23F3',
    'task completed': '\u2705', 'task rejected': '\u274C',
    'dangerous_stop': '\u26A0\uFE0F', 'complex_send': '\uD83D\uDCE8',
    'prompt_ask': '\u2753'
  };
  var STATUS_CLASS = {
    'task accepted': 'accepted', 'task on going': 'ongoing',
    'task completed': 'completed', 'task rejected': 'rejected',
    'dangerous_stop': 'dangerous', 'complex_send': 'complex',
    'prompt_ask': 'prompt'
  };
  var STATUS_LABEL = {
    'task accepted': 'Accepted', 'task on going': 'On Going',
    'task completed': 'Completed', 'task rejected': 'Rejected',
    'dangerous_stop': 'Dangerous', 'complex_send': 'Complex',
    'prompt_ask': 'Ask again'
  };

  // ── Styles (self-contained) ───────────────────────
  function injectStyles() {
    var st = document.createElement('style');
    st.id = 'aif-styles';
    st.textContent = [
      '#aif-root{position:fixed;z-index:2147483000;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:14px;color:#222}',
      '#aif-root *{box-sizing:border-box;margin:0;padding:0}',
      '.aif-btn{position:fixed;right:20px;bottom:20px;width:52px;height:52px;border-radius:50%;border:none;background:' + C.theme + ';color:#fff;font-size:22px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.25);transition:transform .15s}',
      '.aif-btn:hover{transform:scale(1.08)}',
      '.aif-panel{position:fixed;right:20px;bottom:84px;width:360px;max-width:calc(100vw - 24px);max-height:70vh;background:#fff;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.28);display:none;flex-direction:column;overflow:hidden}',
      '.aif-panel.show{display:flex}',
      '.aif-header{background:' + C.theme + ';color:#fff;padding:12px 14px;display:flex;justify-content:space-between;align-items:center;font-weight:600}',
      '.aif-header-close{background:none;border:none;color:#fff;font-size:20px;cursor:pointer;line-height:1}',
      '.aif-tabs{display:flex;border-bottom:1px solid #e5e5e5}',
      '.aif-tab{flex:1;padding:10px 4px;background:#fafafa;border:none;cursor:pointer;font-size:13px;color:#666;border-bottom:2px solid transparent}',
      '.aif-tab.active{color:' + C.theme + ';border-bottom-color:' + C.theme + ';background:#fff;font-weight:600}',
      '.aif-body{padding:12px;display:flex;flex-direction:column;gap:8px;overflow-y:auto}',
      '.aif-textarea{width:100%;min-height:90px;border:1px solid #ccc;border-radius:8px;padding:10px;font:inherit;resize:vertical}',
      '.aif-textarea:focus{outline:2px solid ' + C.theme + ';border-color:transparent}',
      '.aif-submit{background:' + C.theme + ';color:#fff;border:none;border-radius:8px;padding:10px;font:inherit;font-weight:600;cursor:pointer}',
      '.aif-submit:disabled{opacity:.6;cursor:wait}',
      '.aif-msg{min-height:18px;font-size:12px}',
      '.aif-msg.ok{color:#155724}.aif-msg.error{color:#b91c1c}',
      '.aif-reply-info{background:#fff3cd;padding:8px 10px;border-radius:6px;font-size:12px;color:#856404;display:flex;justify-content:space-between;align-items:center}',
      '.aif-reply-info button{background:none;border:none;cursor:pointer;font-size:16px;color:#856404}',
      '.aif-tasks-list{list-style:none;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:8px;max-height:52vh}',
      '.aif-empty{color:#999;text-align:center;padding:24px 8px;font-size:13px}',
      '.aif-task{border:1px solid #eee;border-radius:8px;padding:10px}',
      '.aif-task-prompt{font-size:13px;margin-bottom:6px;word-break:break-word}',
      '.aif-task-meta{font-size:11px;color:#888;display:flex;align-items:center;gap:6px;flex-wrap:wrap}',
      '.aif-badge{padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}',
      '.aif-badge.accepted{background:#fff3cd;color:#856404}',
      '.aif-badge.ongoing{background:#cce5ff;color:#004085}',
      '.aif-badge.completed{background:#d4edda;color:#155724}',
      '.aif-badge.rejected{background:#f8d7da;color:#721c24}',
      '.aif-badge.dangerous{background:#fee2e2;color:#b91c1c}',
      '.aif-badge.complex{background:#e9d5ff;color:#6b21a8}',
      '.aif-badge.prompt{background:#e0f2fe;color:#075985}',
      '.aif-reply-btn{background:none;border:1px solid ' + C.theme + ';color:' + C.theme + ';border-radius:6px;padding:2px 8px;font-size:11px;cursor:pointer}',
      '.aif-reply-task{margin-left:16px;border-left:3px solid ' + C.theme + '}',
      '.aif-set-row{display:flex;flex-direction:column;gap:4px}',
      '.aif-set-row label{font-size:12px;color:#666;font-weight:600}',
      '.aif-set-row input{border:1px solid #ccc;border-radius:6px;padding:8px;font:inherit}',
      '@media(max-width:420px){.aif-panel{right:8px;left:8px;width:auto;bottom:76px}}'
    ].join('\n');
    document.head.appendChild(st);
  }

  // ── DOM ───────────────────────────────────────────
  function build() {
    if (document.getElementById('aif-root')) return;
    var r = document.createElement('div');
    r.id = 'aif-root';
    r.innerHTML =
      '<button class="aif-btn" id="aif-toggle" title="AI Instant Fix" aria-label="Open AI Instant Fix">\uD83D\uDD27</button>' +
      '<div class="aif-panel" id="aif-panel">' +
        '<div class="aif-header"><span>AI Instant Fix</span>' +
          '<button class="aif-header-close" id="aif-close" aria-label="Close">&times;</button></div>' +
        '<div class="aif-tabs">' +
          '<button class="aif-tab active" data-tab="new">+ New Fix</button>' +
          '<button class="aif-tab" data-tab="tasks">Tasks</button>' +
          '<button class="aif-tab" data-tab="settings">Settings</button>' +
        '</div>' +
        '<div id="aif-tab-new" class="aif-body">' +
          '<div id="aif-reply-info" class="aif-reply-info" style="display:none">' +
            '<span>Replying to task #<span id="aif-reply-id"></span></span>' +
            '<button id="aif-cancel-reply" aria-label="Cancel reply">&times;</button></div>' +
          '<textarea class="aif-textarea" id="aif-prompt" placeholder="Describe the fix you want..."></textarea>' +
          '<button class="aif-submit" id="aif-submit">Submit</button>' +
          '<div class="aif-msg" id="aif-msg"></div>' +
        '</div>' +
        '<div id="aif-tab-tasks" class="aif-body" style="display:none;padding:0">' +
          '<ul class="aif-tasks-list" id="aif-tasks"></ul>' +
        '</div>' +
        '<div id="aif-tab-settings" class="aif-body" style="display:none">' +
          '<div class="aif-set-row"><label>API server</label><input id="aif-set-api" value=""></div>' +
          '<div class="aif-set-row"><label>User ID</label><input id="aif-set-uid" value=""></div>' +
          '<div class="aif-set-row"><label>Access token (JWT, optional)</label><input id="aif-set-token" type="password" value=""></div>' +
          '<button class="aif-submit" id="aif-save-settings">Save</button>' +
          '<div class="aif-msg" id="aif-settings-msg"></div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(r);
  }

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = String(s == null ? '' : s);
    return d.innerHTML;
  }

  function ago(dateStr) {
    var diff = Date.now() - new Date(String(dateStr) + 'Z').getTime();
    var mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return mins + 'm ago';
    if (mins < 1440) return Math.floor(mins / 60) + 'h ago';
    return Math.floor(mins / 1440) + 'd ago';
  }

  function msg(elId, text, isOk) {
    var el = document.getElementById(elId);
    if (!el) return;
    el.textContent = text;
    el.className = 'aif-msg ' + (isOk ? 'ok' : 'error');
    setTimeout(function () { el.className = 'aif-msg'; }, 5000);
  }

  // ── API ───────────────────────────────────────────
  function api(method, path, params, body) {
    var url = C.api + path;
    if (params) {
      var qs = Object.keys(params).map(function (k) {
        return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
      }).join('&');
      if (qs) url += (url.indexOf('?') === -1 ? '?' : '&') + qs;
    }
    var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
    if (C.token) opts.headers['Authorization'] = 'Bearer ' + C.token;
    if (body) opts.body = JSON.stringify(body);
    return fetch(url, opts).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var err = new Error(data.error || ('HTTP ' + res.status));
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  // ── Render ────────────────────────────────────────
  function render() {
    var el = document.getElementById('aif-tasks');
    if (!el) return;
    if (!tasks.length) {
      el.innerHTML = '<li class="aif-empty">No tasks for this page</li>';
      return;
    }
    var html = '';
    for (var i = 0; i < tasks.length; i++) {
      var t = tasks[i];
      var isReply = t.parent_id ? ' \u21B3 reply to #' + t.parent_id : '';
      var actions = '';
      if (t.status === 'task completed' || t.status === 'task rejected') {
        actions = '<button class="aif-reply-btn" data-id="' + t.id + '">Reply</button>';
      }
      html +=
        '<li class="aif-task' + (t.parent_id ? ' aif-reply-task' : '') + '">' +
          '<div class="aif-task-prompt">' + (STATUS_EMOJI[t.status] || '') + ' ' + esc(t.prompt) + '</div>' +
          '<div class="aif-task-meta">' +
            '<span class="aif-badge ' + (STATUS_CLASS[t.status] || 'accepted') + '">' + (STATUS_LABEL[t.status] || esc(t.status)) + '</span>' +
            '<span>' + ago(t.created_at) + isReply + '</span>' + actions +
          '</div>' +
        '</li>';
    }
    el.innerHTML = html;
    var btns = el.querySelectorAll('.aif-reply-btn');
    for (var j = 0; j < btns.length; j++) {
      btns[j].addEventListener('click', (function (btn) {
        return function () { startReply(parseInt(btn.dataset.id, 10)); };
      })(btns[j]));
    }
  }

  function loadTasks() {
    api('GET', '/api/tasks', { page_url: window.location.href, limit: 50 })
      .then(function (d) { tasks = d.tasks || []; render(); })
      .catch(function (e) {
        if (e.status === 401) {
          var el = document.getElementById('aif-tasks');
          if (el) el.innerHTML = '<li class="aif-empty">Auth required — set a token in Settings.</li>';
        }
      });
  }

  // ── Reply ─────────────────────────────────────────
  function startReply(parentId) {
    replyTo = parentId;
    switchTab('new');
    document.getElementById('aif-reply-info').style.display = 'flex';
    document.getElementById('aif-reply-id').textContent = parentId;
    document.getElementById('aif-prompt').placeholder = 'Issue with task #' + parentId + '? Describe it...';
    document.getElementById('aif-prompt').focus();
  }

  function cancelReply() {
    replyTo = null;
    document.getElementById('aif-reply-info').style.display = 'none';
    document.getElementById('aif-prompt').placeholder = 'Describe the fix you want...';
  }

  // ── Submit ────────────────────────────────────────
  function submit() {
    var ta = document.getElementById('aif-prompt');
    var btn = document.getElementById('aif-submit');
    var prompt = ta.value.trim();
    if (!prompt) { msg('aif-msg', 'Please describe the fix.', false); return; }

    btn.disabled = true;
    btn.textContent = 'Sending...';

    var body = {
      user_id: C.uid,
      prompt: prompt,
      url: window.location.href,
      page_url: window.location.href
    };
    if (replyTo) body.parent_id = replyTo;

    api('POST', '/api/tasks', null, body)
      .then(function (r) {
        if (r.task_id) {
          var extra = replyTo ? ' (reply to #' + replyTo + ')' : '';
          msg('aif-msg', '\u2713 Task #' + r.task_id + ' submitted!' + extra, true);
          ta.value = '';
          cancelReply();
          loadTasks();
        } else {
          msg('aif-msg', r.error || 'Unknown error', false);
        }
      })
      .catch(function (e) {
        if (e.status === 401) msg('aif-msg', 'Auth required — set a token in Settings.', false);
        else msg('aif-msg', 'Network error: ' + e.message, false);
      })
      .then(function () { btn.disabled = false; btn.textContent = 'Submit'; });
  }

  // ── Tabs ──────────────────────────────────────────
  function switchTab(name) {
    var tabs = document.querySelectorAll('#aif-root .aif-tab');
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].classList.toggle('active', tabs[i].dataset.tab === name);
    }
    ['new', 'tasks', 'settings'].forEach(function (k) {
      var el = document.getElementById('aif-tab-' + k);
      if (el) el.style.display = (k === name) ? 'flex' : 'none';
    });
    if (name === 'tasks') loadTasks();
    if (name === 'settings') loadSettingsUI();
  }

  // ── Settings ──────────────────────────────────────
  function loadSettingsUI() {
    document.getElementById('aif-set-api').value = C.api;
    document.getElementById('aif-set-uid').value = C.uid;
    document.getElementById('aif-set-token').value = C.token;
  }

  function applySettings() {
    var apiVal = document.getElementById('aif-set-api').value.trim();
    var uidVal = document.getElementById('aif-set-uid').value.trim();
    var tokVal = document.getElementById('aif-set-token').value.trim();
    if (apiVal) C.api = apiVal.replace(/\/+$/, '');
    if (uidVal) C.uid = uidVal;
    C.token = tokVal;
    msg('aif-settings-msg', 'Saved.', true);
  }

  // ── Polling ───────────────────────────────────────
  function startPolling() { stopPolling(); pollTimer = setInterval(function () { if (open) loadTasks(); }, 8000); }
  function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

  // ── Events ────────────────────────────────────────
  function setupEvents() {
    document.getElementById('aif-toggle').addEventListener('click', function () {
      open = !open;
      document.getElementById('aif-panel').classList.toggle('show', open);
      if (open) { loadTasks(); startPolling(); } else { stopPolling(); }
    });
    document.getElementById('aif-close').addEventListener('click', function () {
      open = false;
      document.getElementById('aif-panel').classList.remove('show');
      stopPolling();
    });
    var tabs = document.querySelectorAll('#aif-root .aif-tab');
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].addEventListener('click', (function (tab) {
        return function () { switchTab(tab.dataset.tab); };
      })(tabs[i]));
    }
    document.getElementById('aif-submit').addEventListener('click', submit);
    document.getElementById('aif-prompt').addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); submit(); }
    });
    document.getElementById('aif-cancel-reply').addEventListener('click', cancelReply);
    document.getElementById('aif-save-settings').addEventListener('click', applySettings);
  }

  function init() {
    injectStyles();
    build();
    setupEvents();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

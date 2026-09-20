/**
 * Fizi AI Instant Fix — Universal Browser Widget v6.0
 * Zero dependencies. Inject into any page via single <script> tag.
 */
(function () {
  'use strict';

  var S = document.currentScript;
  var WC = window.AIF_CONFIG || {};
  var LS = {};
  try { LS = JSON.parse(localStorage.getItem('aif_settings') || '{}'); } catch (e) {}

  if (S && S.dataset.aifApi) { localStorage.removeItem('aif_settings'); LS = {}; }

  var C = {
    api:     (S && S.dataset.aifApi)     || LS.api     || WC.api     || 'http://localhost:5556',
    uid:     (S && S.dataset.aifUserId)  || LS.userId  || WC.userId  || 'anonymous',
    profile: LS.profile || (S && S.dataset.aifProfile) || WC.profile || 'default',
    token:   LS.token   || (S && S.dataset.aifToken)   || WC.token   || '',
    theme:   LS.theme   || (S && S.dataset.aifTheme)   || WC.theme   || '#247b70',
    url:     window.location.href,
  };

  function saveSettings() {
    localStorage.setItem('aif_settings', JSON.stringify({
      api: C.api, userId: C.uid, profile: C.profile, token: C.token, theme: C.theme
    }));
  }

  var tasks = [];
  var activeTab = 'new';
  var open = false;
  var pollTimer = null;
  var replyTo = null; // parent task ID when replying

  function startPolling() { stopPolling(); pollTimer = setInterval(function () { if (open) loadTasks(); }, 8000); }
  function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

  // ── DOM ───────────────────────────────────────────
  function build() {
    if (document.getElementById('aif-root')) return;
    var r = document.createElement('div');
    r.id = 'aif-root';
    r.innerHTML =
      '<button class="aif-btn" id="aif-toggle" title="Fizi AI Instant Fix">\uD83D\uDD27</button>' +
      '<div class="aif-panel" id="aif-panel">' +
        '<div class="aif-header">' +
          '<span>Fizi AI Instant Fix</span>' +
          '<button class="aif-header-close" id="aif-close">&times;</button>' +
        '</div>' +
        '<div class="aif-tabs">' +
          '<button class="aif-tab active" data-tab="new">+ New Fix</button>' +
          '<button class="aif-tab" data-tab="tasks">Tasks</button>' +
          
        '</div>' +
        '<div id="aif-tab-new" class="aif-body">' +
          '<div id="aif-reply-info" style="display:none;background:#fff3cd;padding:8px 10px;border-radius:6px;font-size:12px;color:#856404;margin-bottom:4px;">' +
            'Replying to task #<span id="aif-reply-id"></span> <button id="aif-cancel-reply" style="float:right;background:none;border:none;cursor:pointer;font-size:16px;">&times;</button>' +
          '</div>' +
          '<textarea class="aif-textarea" id="aif-prompt" ' +
            'placeholder="Describe the fix you want..."></textarea>' +
          '<button class="aif-submit" id="aif-submit">Submit</button>' +
          '<div class="aif-msg" id="aif-msg"></div>' +
        '</div>' +
        '<div id="aif-tab-tasks" class="aif-tasks-wrap">' +
          '<div class="aif-tasks-head">' +
            '<span id="aif-tasks-title">Tasks on this page</span>' +
            '<span id="aif-count">0</span>' +
          '</div>' +
          '<ul class="aif-tasks-list" id="aif-tasks"></ul>' +
        '</div>' +
      '</div>';
    document.body.appendChild(r);
  }

  // ── Status display ────────────────────────────────
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
    'dangerous_stop': 'Dangerous;STOP', 'complex_send': 'Complex;Send to Fizi',
    'prompt_ask': 'Prompt difficult;Ask again'
  };

  function esc(s) {
    var d = document.createElement('div'); d.textContent = s; return d.innerHTML;
  }

  function ago(dateStr) {
    var diff = Date.now() - new Date(dateStr + 'Z').getTime();
    var mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now'; if (mins < 60) return mins + 'm ago';
    if (mins < 1440) return Math.floor(mins / 60) + 'h ago';
    return Math.floor(mins / 1440) + 'd ago';
  }

  function msg(elId, text, isOk) {
    var el = document.getElementById(elId);
    el.textContent = text;
    el.className = 'aif-msg ' + (isOk ? 'ok' : 'error');
    setTimeout(function () { el.className = 'aif-msg'; }, 5000);
  }

  // ── API ───────────────────────────────────────────
  function api(method, action, params, body) {
    var xhr = new XMLHttpRequest();
    return new Promise(function (resolve, reject) {
      var url = C.api;
      var qs = 'aif_action=' + action;
      if (params) {
        Object.keys(params).forEach(function (k) {
          qs += '&' + encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
        });
      }
      url += (url.indexOf('?') === -1 ? '?' : '&') + qs;
      xhr.open(method, url, true);
      xhr.setRequestHeader('Content-Type', 'application/json');
      if (C.token) xhr.setRequestHeader('Authorization', 'Bearer ' + C.token);
      xhr.onload = function () { try { resolve(JSON.parse(xhr.responseText)); } catch (e) { reject(e); } };
      xhr.onerror = function () { reject(new Error('Network error')); };
      xhr.send(body ? JSON.stringify(body) : null);
    });
  }

  // ── Render tasks ──────────────────────────────────
  function render() {
    var el = document.getElementById('aif-tasks');
    var cnt = document.getElementById('aif-count');
    cnt.textContent = tasks.length + ' tasks';
    if (!tasks.length) {
      el.innerHTML = '<li class="aif-empty">No tasks for this page</li>'; return;
    }
    var html = '';
    for (var i = 0; i < tasks.length; i++) {
      var t = tasks[i];
      var isReply = t.parent_id ? ' \u21B3 reply to #' + t.parent_id : '';
      var actions = '';
      if (t.status === 'task completed' || t.status === 'task rejected') {
        actions = '<button class="aif-reply-btn" data-id="' + t.id + '" title="Respond/report issue">\uD83D\uDCE2 Reply</button>';
      }
      html +=
        '<li class="aif-task' + (t.parent_id ? ' aif-reply-task' : '') + '">' +
          '<div class="aif-task-prompt">' + (STATUS_EMOJI[t.status] || '') + ' ' + esc(t.prompt) + '</div>' +
          '<div class="aif-task-meta">' +
            '<span class="aif-badge ' + (STATUS_CLASS[t.status] || 'accepted') + '">' + (STATUS_LABEL[t.status] || t.status) + '</span>' +
            ' &middot; ' + ago(t.created_at) + isReply +
            ' ' + actions +
          '</div>' +
        '</li>';
    }
    el.innerHTML = html;

    // Bind reply buttons
    var btns = el.querySelectorAll('.aif-reply-btn');
    for (var j = 0; j < btns.length; j++) {
      btns[j].onclick = (function (tid) {
        return function () { startReply(tid); };
      })(parseInt(btns[j].dataset.id));
    }
  }

  function loadTasks() {
    api('GET', 'list', { page_url: C.url, limit: 50 })
      .then(function (d) { tasks = d.tasks || []; render(); })
      ['catch'](function (e) { console.warn('Fizi AI:', e); });
  }

  // ── Reply ─────────────────────────────────────────
  function startReply(parentId) {
    replyTo = parentId;
    document.getElementById('aif-reply-info').style.display = 'block';
    document.getElementById('aif-reply-id').textContent = parentId;
    document.getElementById('aif-prompt').placeholder = 'Describe the issue with task #' + parentId + '...';
    document.getElementById('aif-prompt').focus();
    document.getElementById('aif-tab-new').style.display = 'flex';
    document.getElementById('aif-tab-tasks').classList.remove('show');
    document.getElementById('aif-tab-settings').style.display = 'none';
    // Switch to New Fix tab
    var tabs = document.querySelectorAll('.aif-tab');
    for (var i = 0; i < tabs.length; i++) { tabs[i].classList.remove('active'); }
    tabs[0].classList.add('active');
    activeTab = 'new';
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

    btn.disabled = true; btn.textContent = 'Sending...';

    var body = { user_id: C.uid, prompt: prompt, url: C.url, page_url: C.url, profile: C.profile };
    if (replyTo) body.parent_id = replyTo;

    api('POST', 'create', { aif_user: C.uid }, body)
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
      ['catch'](function (e) { msg('aif-msg', 'Network error: ' + e.message, false); })
      .then(function () { btn.disabled = false; btn.textContent = 'Submit'; });
  }

  // ── Settings ──────────────────────────────────────
  function loadSettingsUI() {
    document.getElementById('aif-set-api').value = C.api;
    document.getElementById('aif-set-uid').value = C.uid;
  }
  function applySettings() {
    C.api = document.getElementById('aif-set-api').value.trim() || C.api;
    C.uid = document.getElementById('aif-set-uid').value.trim() || C.uid;
    saveSettings(); msg('aif-settings-msg', 'Saved!', true);
  }
  function testConnection() {
    var btn = document.getElementById('aif-test-connection');
    btn.disabled = true; btn.textContent = '...';
    api('GET', 'health').then(function (r) {
      if (r.status === 'ok') msg('aif-settings-msg', 'Connection OK', true);
      else msg('aif-settings-msg', 'Unexpected: ' + JSON.stringify(r), false);
    })['catch'](function (e) {
      msg('aif-settings-msg', 'Failed: ' + e.message, false);
    }).then(function () { btn.disabled = false; btn.textContent = 'Test'; });
  }

  // ── Events ────────────────────────────────────────
  function setupEvents() {
    document.getElementById('aif-toggle').onclick = function () {
      open = !open;
      document.getElementById('aif-panel').classList.toggle('show', open);
      document.getElementById('aif-toggle').classList.toggle('open', open);
      if (open) { loadTasks(); startPolling(); }
      else { stopPolling(); }
    };
    document.getElementById('aif-close').onclick = function () {
      open = false;
      document.getElementById('aif-panel').classList.remove('show');
      document.getElementById('aif-toggle').classList.remove('open');
      stopPolling();
    };

    // Tabs
    var tabMap = { 'new': 'aif-tab-new', 'tasks': 'aif-tab-tasks' };
    var tabs = document.querySelectorAll('.aif-tab');
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].onclick = (function (t) { return function () {
        activeTab = t.dataset.tab;
        for (var j = 0; j < tabs.length; j++) tabs[j].classList.remove('active');
        t.classList.add('active');
        Object.keys(tabMap).forEach(function (k) {
          var el = document.getElementById(tabMap[k]);
          if (k === 'tasks') el.classList.toggle('show', activeTab === 'tasks');
          else el.style.display = activeTab === k ? 'flex' : 'none';
        });
        if (activeTab === 'tasks') loadTasks();
        
      }; })(tabs[i]);
    }

    document.getElementById('aif-submit').onclick = submit;
    document.getElementById('aif-prompt').onkeydown = function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); submit(); }
    };
    document.getElementById('aif-cancel-reply').onclick = cancelReply;
    document.getElementById('aif-save-settings').onclick = applySettings;
    document.getElementById('aif-test-connection').onclick = testConnection;
  }

  build();
  setupEvents();
  console.log('Fizi AI Instant Fix ready — ' + C.api);
})();

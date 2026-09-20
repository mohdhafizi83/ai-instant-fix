<?php
/**
 * Fizi AI Instant Fix — Universal (Standalone)
 * Zero dependencies. Works on ANY PHP 7.0+ server.
 * 
 * Usage:
 *   1. Drop this file on any PHP server (e.g., https://yourserver.com/fizi-ai.php)
 *   2. Inject widget via <script> tag:
 *      <script src="https://yourserver.com/fizi-ai.php?load=widget" 
 *              data-aif-api="https://yourserver.com/fizi-ai.php"></script>
 *   3. Or visit directly for admin panel
 * 
 * Database: auto-creates SQLite file (fizi-ai.db) in same directory.
 * For MySQL, set env vars: DB_HOST, DB_NAME, DB_USER, DB_PASS
 */

// ═══════════════════════════════════════════════════════
//  CONFIG — change these or set env vars
// ═══════════════════════════════════════════════════════
define('DB_ENGINE',   getenv('DB_ENGINE')   ?: 'sqlite');  // sqlite or mysql
define('SQLITE_FILE', __DIR__ . '/fizi-ai.db');
define('MYSQL_HOST',  getenv('DB_HOST')  ?: '127.0.0.1');
define('MYSQL_NAME',  getenv('DB_NAME')  ?: 'fizi_ai');
define('MYSQL_USER',  getenv('DB_USER')  ?: 'root');
define('MYSQL_PASS',  getenv('DB_PASS')  ?: '');

// Who can use the widget? Options: 'all', 'basic_auth', 'ip_whitelist'
define('ACCESS_MODE', getenv('ACCESS_MODE') ?: 'all');
define('BASIC_AUTH_USER', getenv('AUTH_USER') ?: 'admin');
define('BASIC_AUTH_PASS', getenv('AUTH_PASS') ?: '');
define('IP_WHITELIST', getenv('IP_WHITELIST') ?: '127.0.0.1,::1');

// Task processing: URL to ping when new task created (optional)
define('WEBHOOK_URL', getenv('WEBHOOK_URL') ?: '');

// ═══════════════════════════════════════════════════════
//  DATABASE
// ═══════════════════════════════════════════════════════
function db() {
    static $pdo = null;
    if ($pdo) return $pdo;

    if (DB_ENGINE === 'mysql') {
        $dsn = 'mysql:host=' . MYSQL_HOST . ';dbname=' . MYSQL_NAME . ';charset=utf8mb4';
        $pdo = new PDO($dsn, MYSQL_USER, MYSQL_PASS, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
    } else {
        $pdo = new PDO('sqlite:' . SQLITE_FILE, null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $pdo->exec('PRAGMA journal_mode=WAL');
    }

    $pdo->exec("CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY " . (DB_ENGINE === 'mysql' ? 'AUTO_INCREMENT' : 'AUTOINCREMENT') . ",
        parent_id INTEGER DEFAULT NULL,
        user_id TEXT NOT NULL DEFAULT 'anonymous',
        prompt TEXT NOT NULL,
        url TEXT NOT NULL,
        page_url TEXT,
        path_files TEXT,
        status TEXT NOT NULL DEFAULT 'task accepted',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )");

    // Migration: add columns if missing (SQLite only)
    if (DB_ENGINE === 'sqlite') {
        $cols = [];
        foreach ($pdo->query("PRAGMA table_info(tasks)") as $c) $cols[] = $c['name'];
        if (!in_array('parent_id', $cols)) $pdo->exec('ALTER TABLE tasks ADD COLUMN parent_id INTEGER DEFAULT NULL');
        if (!in_array('page_url', $cols)) $pdo->exec('ALTER TABLE tasks ADD COLUMN page_url TEXT');
    }

    return $pdo;
}

// ═══════════════════════════════════════════════════════
//  ACCESS CONTROL
// ═══════════════════════════════════════════════════════
function check_access() {
    if (ACCESS_MODE === 'all') return true;

    if (ACCESS_MODE === 'basic_auth') {
        if (BASIC_AUTH_PASS === '') {
            // Fail closed: basic auth selected but no password configured.
            http_response_code(501);
            die(json_encode(['error' => 'AUTH_PASS not configured']));
        }
        if (!isset($_SERVER['PHP_AUTH_USER']) ||
            !hash_equals(BASIC_AUTH_USER, (string)$_SERVER['PHP_AUTH_USER']) ||
            !hash_equals(BASIC_AUTH_PASS, (string)($_SERVER['PHP_AUTH_PW'] ?? ''))) {
            header('WWW-Authenticate: Basic realm="Fizi AI"');
            http_response_code(401);
            die(json_encode(['error' => 'Authentication required']));
        }
        return true;
    }

    if (ACCESS_MODE === 'ip_whitelist') {
        $ip = $_SERVER['REMOTE_ADDR'] ?? '';
        $allowed = array_map('trim', explode(',', IP_WHITELIST));
        if (!in_array($ip, $allowed)) {
            http_response_code(403);
            die(json_encode(['error' => 'IP not allowed: ' . $ip]));
        }
        return true;
    }

    return true;
}

// ═══════════════════════════════════════════════════════
//  API HANDLER
// ═══════════════════════════════════════════════════════
function handle_api() {
    $action = $_GET['aif_action'] ?? $_POST['aif_action'] ?? '';
    if (!$action) return;

    header('Content-Type: application/json; charset=utf-8');
    header('Access-Control-Allow-Origin: *');
    header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type, Authorization');
    if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { http_response_code(204); exit; }

    check_access();

    $db  = db();
    $raw = file_get_contents('php://input');
    $in  = json_decode($raw, true) ?: [];

    $valid_status = [
        'task accepted','task on going','task completed','task rejected',
        'dangerous_stop','complex_send','prompt_ask'
    ];

    try {
        switch ($action) {
        case 'health':
            echo json_encode(['status' => 'ok', 'engine' => DB_ENGINE, 'server' => 'fizi-universal']);
            break;

        case 'create':
            $uid  = trim($_GET['aif_user'] ?? $in['aif_user'] ?? $in['user_id'] ?? 'anonymous');
            $p    = trim($in['prompt'] ?? '');
            $url  = trim($in['url'] ?? '');
            $page = trim($in['page_url'] ?? $url);
            $pid  = ($in['parent_id'] ?? null) ? (int)$in['parent_id'] : null;

            if (!$p || !$url) { echo json_encode(['error'=>'prompt and url required']); break; }

            $stmt = $db->prepare('INSERT INTO tasks (parent_id, user_id, prompt, url, page_url, path_files, status) VALUES (?, ?, ?, ?, ?, ?, ?)');
            $stmt->execute([$pid, $uid, $p, $url, $page, $in['path_files'] ?? '', 'task accepted']);
            $tid = $db->lastInsertId();

            // Fire webhook if configured
            $webhook_ok = false;
            if (WEBHOOK_URL) {
                $ctx = stream_context_create([
                    'http' => [
                        'method'  => 'POST',
                        'header'  => 'Content-Type: application/json',
                        'content' => json_encode(['user_id'=>$uid, 'prompt'=>$p, 'url'=>$url, 'remote_task_id'=>$tid]),
                        'timeout' => 5,
                    ]
                ]);
                @file_get_contents(WEBHOOK_URL, false, $ctx);
                $webhook_ok = true; // fire-and-forget
            }

            echo json_encode(['task_id' => (int)$tid, 'status' => 'task accepted']);
            break;

        case 'list':
            $uid  = $_GET['aif_user'] ?? '';
            $st   = $_GET['status'] ?? '';
            $page = $_GET['page_url'] ?? '';
            $pid  = $_GET['parent_id'] ?? '';
            $lim  = min((int)($_GET['limit'] ?? 50), 200);

            $where = ['1=1'];
            $params = [];
            if ($uid)  { $where[] = 'user_id = ?'; $params[] = $uid; }
            if ($st)   { $where[] = 'status = ?';  $params[] = $st; }
            if ($page) { $where[] = 'page_url = ?'; $params[] = $page; }
            if ($pid)  { $where[] = '(id = ? OR parent_id = ?)'; $params[] = (int)$pid; $params[] = (int)$pid; }

            $where_sql = implode(' AND ', $where);
            $params[] = $lim;
            $stmt = $db->prepare("SELECT * FROM tasks WHERE $where_sql ORDER BY created_at DESC LIMIT ?");
            $stmt->execute($params);
            $tasks = $stmt->fetchAll();

            // Counts
            $cnt_where = ['1=1'];
            $cnt_params = [];
            if ($uid)  { $cnt_where[] = 'user_id = ?'; $cnt_params[] = $uid; }
            if ($page) { $cnt_where[] = 'page_url = ?'; $cnt_params[] = $page; }
            $cnt_sql = implode(' AND ', $cnt_where);
            $stmt = $db->prepare("SELECT status, COUNT(*) c FROM tasks WHERE $cnt_sql GROUP BY status");
            $stmt->execute($cnt_params);
            $counts = [];
            foreach ($stmt->fetchAll() as $r) $counts[$r['status']] = (int)$r['c'];

            echo json_encode(['tasks' => $tasks, 'counts' => $counts]);
            break;

        case 'get':
            $id = (int)($_GET['id'] ?? 0);
            $stmt = $db->prepare('SELECT * FROM tasks WHERE id = ?');
            $stmt->execute([$id]);
            $task = $stmt->fetch();
            if (!$task) { echo json_encode(['error'=>'not found']); break; }

            // Fetch replies
            $stmt = $db->prepare('SELECT * FROM tasks WHERE parent_id = ? ORDER BY created_at ASC');
            $stmt->execute([$id]);
            $task['replies'] = $stmt->fetchAll();

            echo json_encode($task);
            break;

        case 'update':
            $id = (int)($_GET['id'] ?? 0);
            $st = trim($in['status'] ?? '');
            if (!$id || !in_array($st, $valid_status)) {
                echo json_encode(['error'=>'invalid status']); break;
            }
            $stmt = $db->prepare("UPDATE tasks SET status = ?, updated_at = datetime('now') WHERE id = ?");
            $stmt->execute([$st, $id]);
            echo json_encode(['task_id' => $id, 'status' => $st]);
            break;

        default:
            echo json_encode(['error'=>'unknown action: '.$action]);
        }
    } catch (Exception $e) {
        http_response_code(500);
        echo json_encode(['error' => $e->getMessage()]);
    }
    exit;
}

// ═══════════════════════════════════════════════════════
//  WIDGET LOADER — serve widget JS/CSS
// ═══════════════════════════════════════════════════════
function serve_widget() {
    $load = $_GET['load'] ?? '';
    if ($load === 'widget') {
        header('Content-Type: application/javascript; charset=utf-8');
        header('Cache-Control: public, max-age=3600');
        readfile(__DIR__ . '/fizi-widget.js');
        exit;
    }
    if ($load === 'css') {
        header('Content-Type: text/css; charset=utf-8');
        header('Cache-Control: public, max-age=3600');
        readfile(__DIR__ . '/fizi-widget.css');
        exit;
    }
}

// ═══════════════════════════════════════════════════════
//  ADMIN PANEL (simple HTML)
// ═══════════════════════════════════════════════════════
function admin_panel() {
    if (isset($_GET['aif_action'])) return; // API mode
    if (isset($_GET['load'])) return;       // Widget mode

    check_access();

    $script = basename(__FILE__);
    $api_url = (isset($_SERVER['HTTPS']) ? 'https' : 'http') . '://' . $_SERVER['HTTP_HOST'] . $_SERVER['SCRIPT_NAME'];

    ?><!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Fizi AI Instant Fix</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f0f0f1; color: #333; padding: 40px; max-width: 800px; margin: 0 auto; }
        h1 { color: #247b70; margin-bottom: 10px; }
        .card { background: #fff; border-radius: 8px; padding: 24px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .card h2 { font-size: 16px; margin-bottom: 12px; color: #555; }
        code { background: #f0f0f1; padding: 2px 6px; border-radius: 4px; font-size: 13px; word-break: break-all; }
        pre { background: #1a202c; color: #e2e8f0; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 12px; line-height: 1.6; }
        .badge { display: inline-block; padding: 3px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
        .badge-ok { background: #d4edda; color: #155724; }
        .badge-warn { background: #fff3cd; color: #856404; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #eee; }
        th { background: #f8f9fa; font-weight: 600; color: #555; }
        .status-accepted { color: #856404; } .status-ongoing { color: #004085; }
        .status-completed { color: #155724; } .status-rejected { color: #721c24; }
        .status-dangerous { color: #b91c1c; font-weight: 700; }
    </style>
</head>
<body>
    <h1>🔧 Fizi AI Instant Fix</h1>
    <p style="color:#666;margin-bottom:24px;">Universal — works on any PHP server. Zero dependencies.</p>

    <div class="card">
        <h2>📋 Widget Injection</h2>
        <p>Add this to any webpage to show the widget:</p>
        <pre>&lt;script src="<?php echo $api_url; ?>?load=widget"
        data-aif-api="<?php echo $api_url; ?>"
        data-aif-user-id="admin"&gt;&lt;/script&gt;
&lt;link rel="stylesheet" href="<?php echo $api_url; ?>?load=css"&gt;</pre>
    </div>

    <div class="card">
        <h2>🔌 API Endpoints</h2>
        <table>
            <tr><th>Action</th><th>Method</th><th>URL</th></tr>
            <tr><td>Health</td><td>GET</td><td><code><?php echo $api_url; ?>?aif_action=health</code></td></tr>
            <tr><td>Create</td><td>POST</td><td><code><?php echo $api_url; ?>?aif_action=create</code></td></tr>
            <tr><td>List</td><td>GET</td><td><code><?php echo $api_url; ?>?aif_action=list</code></td></tr>
            <tr><td>Get</td><td>GET</td><td><code><?php echo $api_url; ?>?aif_action=get&id=1</code></td></tr>
            <tr><td>Update</td><td>POST</td><td><code><?php echo $api_url; ?>?aif_action=update&id=1</code></td></tr>
        </table>
    </div>

    <div class="card">
        <h2>📊 Recent Tasks</h2>
        <?php
        try {
            $db = db();
            $tasks = $db->query('SELECT * FROM tasks ORDER BY created_at DESC LIMIT 10')->fetchAll();
            if ($tasks) {
                echo '<table><tr><th>ID</th><th>User</th><th>Prompt</th><th>URL</th><th>Status</th><th>Created</th></tr>';
                foreach ($tasks as $t) {
                    $cls = 'status-' . str_replace([' ','_'], ['-','-'], explode(';', $t['status'])[0]);
                    echo '<tr>';
                    echo '<td>' . $t['id'] . '</td>';
                    echo '<td>' . htmlspecialchars($t['user_id']) . '</td>';
                    echo '<td>' . htmlspecialchars(mb_substr($t['prompt'], 0, 50)) . '</td>';
                    echo '<td>' . htmlspecialchars(parse_url($t['url'], PHP_URL_PATH) ?: $t['url']) . '</td>';
                    echo '<td class="' . $cls . '">' . $t['status'] . '</td>';
                    echo '<td>' . substr($t['created_at'], 0, 16) . '</td>';
                    echo '</tr>';
                }
                echo '</table>';
            } else {
                echo '<p style="color:#999;">No tasks yet.</p>';
            }
        } catch (Exception $e) {
            echo '<p style="color:#b91c1c;">DB error: ' . $e->getMessage() . '</p>';
        }
        ?>
    </div>

    <div class="card">
        <h2>⚙️ Configuration</h2>
        <p>Engine: <span class="badge badge-ok"><?php echo DB_ENGINE; ?></span></p>
        <p>Access: <span class="badge badge-warn"><?php echo ACCESS_MODE; ?></span></p>
        <?php if (WEBHOOK_URL): ?>
        <p>Webhook: <code><?php echo WEBHOOK_URL; ?></code></p>
        <?php endif; ?>
        <p style="margin-top:8px;font-size:12px;color:#999;">Override via env vars: DB_ENGINE, DB_HOST, DB_NAME, DB_USER, DB_PASS, ACCESS_MODE, AUTH_USER, AUTH_PASS, IP_WHITELIST, WEBHOOK_URL</p>
    </div>
</body>
</html>
    <?php
    exit;
}

// ═══════════════════════════════════════════════════════
//  MAIN
// ═══════════════════════════════════════════════════════
serve_widget();
handle_api();
admin_panel();

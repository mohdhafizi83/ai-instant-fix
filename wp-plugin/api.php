<?php
/**
 * AI Instant Fix — API Endpoint (Standalone PHP, direct MySQL)
 * No WordPress dependency. Uses the same MySQL DB as WordPress.
 *
 * All configuration comes from environment variables — never hardcode
 * credentials in this file. See .env.example in the repository root.
 *
 * Endpoints (?action=):
 *   health | create (POST JSON) | list | get | update (POST JSON)
 */

// ── Database config (from environment) ─────────────
define('DB_HOST', getenv('DB_HOST') ?: '127.0.0.1');
define('DB_NAME', getenv('DB_NAME') ?: '');
define('DB_USER', getenv('DB_USER') ?: '');
define('DB_PASS', getenv('DB_PASS') ?: '');
define('DB_PREFIX', getenv('DB_PREFIX') ?: 'wp_');

// ── Integration config (from environment) ──────────
// Executor webhook URL (the AI agent server that processes tasks).
// AIF_HERMES_WEBHOOK is accepted as a legacy alias.
define('AIF_EXECUTOR_WEBHOOK', getenv('AIF_EXECUTOR_WEBHOOK') ?: getenv('AIF_HERMES_WEBHOOK') ?: '');
define('AIF_TELEGRAM_CHAT',  getenv('AIF_TELEGRAM_CHAT') ?: '');
// Shared secret — must match WEBHOOK_SECRET on the executor server.
define('AIF_WEBHOOK_SECRET', getenv('AIF_WEBHOOK_SECRET') ?: '');
$AIF_TELEGRAM_TOKEN = getenv('TELEGRAM_BOT_TOKEN') ?: '';

// ── Headers ────────────────────────────────────────
header('Content-Type: application/json; charset=utf-8');
// Restrict CORS to your site(s) via env; '*' only for local development.
$aif_origin = getenv('AIF_ALLOWED_ORIGIN') ?: '';
if ($aif_origin) {
    header('Access-Control-Allow-Origin: ' . $aif_origin);
} else {
    header('Access-Control-Allow-Origin: *'); // dev default — set AIF_ALLOWED_ORIGIN in production
}
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type, Authorization');
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { http_response_code(204); exit; }

// ── Simple file-based rate limiter (per-IP, sliding window) ──
function aif_rate_limit($limit, $window_sec) {
    $ip = isset($_SERVER['REMOTE_ADDR']) ? preg_replace('/[^0-9a-fA-F.:]/', '', $_SERVER['REMOTE_ADDR']) : 'unknown';
    $file = sys_get_temp_dir() . '/aif_rl_' . md5($ip . __FUNCTION__) . '.json';
    $now = time();
    $hits = [];
    if (is_readable($file)) {
        $data = json_decode((string)file_get_contents($file), true);
        if (is_array($data)) {
            foreach ($data as $t) { if ($now - $t < $window_sec) $hits[] = $t; }
        }
    }
    if (count($hits) >= $limit) return false;
    $hits[] = $now;
    @file_put_contents($file, json_encode($hits), LOCK_EX);
    return true;
}
if (!aif_rate_limit(60, 60)) {
    http_response_code(429);
    echo json_encode(['error' => 'rate limit exceeded']);
    exit;
}

// ── Database connection ────────────────────────────
function aif_db() {
    static $conn = null;
    if ($conn === null) {
        $conn = new mysqli(DB_HOST, DB_USER, DB_PASS, DB_NAME);
        if ($conn->connect_error) {
            error_log('AIF DB connection failed: ' . $conn->connect_error);
            http_response_code(500);
            die(json_encode(['error' => 'DB connection failed']));
        }
        $conn->set_charset('utf8mb4');
    }
    return $conn;
}

function aif_table() {
    $table = DB_PREFIX . 'ai_instant_task';
    $db = aif_db();
    $db->query("CREATE TABLE IF NOT EXISTS $table (
        id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        user_id VARCHAR(255) NOT NULL,
        prompt TEXT NOT NULL,
        url TEXT NOT NULL,
        path_files TEXT DEFAULT NULL,
        status VARCHAR(50) NOT NULL DEFAULT 'task accepted',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
    return $table;
}

// ── Helpers ────────────────────────────────────────
function aif_json($data, $code = 200) {
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

function aif_error($msg, $code = 400) {
    aif_json(['error' => $msg], $code);
}

function aif_get_input() {
    if (in_array($_SERVER['REQUEST_METHOD'], ['POST', 'PUT'])) {
        $raw = file_get_contents('php://input');
        $data = json_decode($raw, true);
        return is_array($data) ? $data : $_POST;
    }
    return $_GET;
}

function aif_trigger_executor($task_id, $user_id, $prompt, $url, $path_files = null) {
    if (!AIF_EXECUTOR_WEBHOOK || !AIF_WEBHOOK_SECRET) return false;

    $payload = json_encode([
        'user_id'        => $user_id,
        'prompt'         => $prompt,
        'url'            => $url,
        'path_files'     => $path_files,
        'remote_task_id' => $task_id,
    ]);
    $signature = hash_hmac('sha256', $payload, AIF_WEBHOOK_SECRET);

    $ch = curl_init(AIF_EXECUTOR_WEBHOOK);
    curl_setopt_array($ch, [
        CURLOPT_POST           => true,
        CURLOPT_POSTFIELDS     => $payload,
        CURLOPT_HTTPHEADER     => [
            'Content-Type: application/json',
            'X-AIF-Signature: ' . $signature,
        ],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => 5,
        CURLOPT_CONNECTTIMEOUT => 3,
    ]);
    curl_exec($ch);
    $http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return $http_code >= 200 && $http_code < 300;
}

function aif_telegram_notify($task) {
    global $AIF_TELEGRAM_TOKEN;
    if (!$AIF_TELEGRAM_TOKEN || !AIF_TELEGRAM_CHAT) return;

    $text = "✅ AI Fix #{$task['id']} selesai\n"
          . "📝 {$task['prompt']}\n"
          . "🌐 {$task['url']}\n"
          . "👤 {$task['user_id']}";

    $ch = curl_init('https://api.telegram.org/bot' . $AIF_TELEGRAM_TOKEN . '/sendMessage');
    curl_setopt_array($ch, [
        CURLOPT_POST           => true,
        CURLOPT_POSTFIELDS     => json_encode(['chat_id' => AIF_TELEGRAM_CHAT, 'text' => $text]),
        CURLOPT_HTTPHEADER     => ['Content-Type: application/json'],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => 10,
    ]);
    curl_exec($ch);
    curl_close($ch);
}

// ═══════════════════════════════════════════════════
//  ROUTER
// ═══════════════════════════════════════════════════

$action = aif_get_input()['action'] ?? '';

switch ($action) {

    case 'health':
        aif_json(['status' => 'ok', 'server' => 'hostinger-php']);
        break;

    case 'create':
        $db    = aif_db();
        $table = aif_table();
        $input = aif_get_input();

        $user_id    = trim($input['user_id'] ?? 'anonymous');
        $prompt     = trim($input['prompt'] ?? '');
        $url        = trim($input['url'] ?? '');
        $path_files = $input['path_files'] ?? null;

        if (!$prompt || !$url) aif_error('prompt and url are required');
        if (strlen($prompt) > 10000) aif_error('prompt too long');

        $stmt = $db->prepare("INSERT INTO $table (user_id, prompt, url, path_files, status) VALUES (?, ?, ?, ?, 'task on going')");
        $stmt->bind_param('ssss', $user_id, $prompt, $url, $path_files);
        $stmt->execute();
        $task_id = $stmt->insert_id;
        $stmt->close();

        $triggered = aif_trigger_executor($task_id, $user_id, $prompt, $url, $path_files);

        aif_json([
            'task_id' => $task_id,
            'status'  => $triggered ? 'task on going' : 'task accepted',
            'warning' => $triggered ? null : 'Executor server unreachable',
        ], 201);
        break;

    case 'list':
        $db      = aif_db();
        $table   = aif_table();
        $user_id = $_GET['user_id'] ?? '';
        $status  = $_GET['status'] ?? '';
        $limit   = min(intval($_GET['limit'] ?? 50), 200);

        $where = 'WHERE 1=1';
        $types = ''; $values = [];
        if ($user_id) { $where .= ' AND user_id = ?'; $types .= 's'; $values[] = $user_id; }
        if ($status)  { $where .= ' AND status = ?';  $types .= 's'; $values[] = $status; }
        $types .= 'i'; $values[] = $limit;

        $stmt = $db->prepare("SELECT * FROM $table $where ORDER BY created_at DESC LIMIT ?");
        $stmt->bind_param($types, ...$values);
        $stmt->execute();
        $tasks = $stmt->get_result()->fetch_all(MYSQLI_ASSOC);
        $stmt->close();

        // Counts (prepared statement — no string interpolation)
        $c_types = ''; $c_values = [];
        $count_where = 'WHERE 1=1';
        if ($user_id) { $count_where .= ' AND user_id = ?'; $c_types .= 's'; $c_values[] = $user_id; }
        $cstmt = $db->prepare("SELECT status, COUNT(*) as cnt FROM $table $count_where GROUP BY status");
        if ($c_values) $cstmt->bind_param($c_types, ...$c_values);
        $cstmt->execute();
        $counts_raw = $cstmt->get_result();
        $counts = [];
        while ($r = $counts_raw->fetch_assoc()) $counts[$r['status']] = intval($r['cnt']);
        $cstmt->close();

        aif_json(['tasks' => $tasks ?: [], 'counts' => $counts]);
        break;

    case 'get':
        $db    = aif_db();
        $table = aif_table();
        $id    = intval($_GET['id'] ?? 0);
        if (!$id) aif_error('id is required');

        $stmt = $db->prepare("SELECT * FROM $table WHERE id = ?");
        $stmt->bind_param('i', $id);
        $stmt->execute();
        $task = $stmt->get_result()->fetch_assoc();
        $stmt->close();

        if (!$task) aif_error('task not found', 404);
        aif_json($task);
        break;

    case 'update':
        $db     = aif_db();
        $table  = aif_table();
        $input  = aif_get_input();
        $id     = intval($_GET['id'] ?? 0);
        $status = trim($input['status'] ?? '');

        if (!$id) aif_error('id is required');
        $valid = ['task accepted', 'task on going', 'task completed', 'task rejected'];
        if (!in_array($status, $valid)) aif_error('Invalid status');

        $stmt = $db->prepare("UPDATE $table SET status = ? WHERE id = ?");
        $stmt->bind_param('si', $status, $id);
        $stmt->execute();
        $stmt->close();

        if ($status === 'task completed') {
            $stmt = $db->prepare("SELECT * FROM $table WHERE id = ?");
            $stmt->bind_param('i', $id);
            $stmt->execute();
            $task = $stmt->get_result()->fetch_assoc();
            $stmt->close();
            if ($task) aif_telegram_notify($task);
        }

        aif_json(['task_id' => $id, 'status' => $status]);
        break;

    default:
        aif_error('Valid actions: health, create, list, get, update');
}

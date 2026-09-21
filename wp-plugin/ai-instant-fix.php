<?php
/**
 * Plugin Name: Fizi AI Instant Fix
 * Version: 7.0
 */

if (!defined('ABSPATH')) exit;

// ── Widget injection ──────────────────────────────
add_action('wp_footer', 'fizi_aif_widget');
add_action('admin_footer', 'fizi_aif_widget');

function fizi_aif_widget() {
    if (!current_user_can('administrator') && !current_user_can('editor')) return;
    if (is_admin()) return;

    $uid = wp_get_current_user()->user_login;
    $api = home_url('/');
    $v   = '20260616v9';
    $js  = esc_url(plugins_url('widget/ai-instant-fix.js', __FILE__) . "?v=$v");
    $css = esc_url(plugins_url('widget/ai-instant-fix.css', __FILE__) . "?v=$v");

    echo "\n<!-- Fizi AI Fix v7 -->\n";
    echo "<link rel='stylesheet' href='$css'>\n";
    echo "<script src='$js' data-aif-api='$api' data-aif-user-id='$uid'></script>\n";
}

// ── API handler ────────────────────────────────────
add_action('init', 'fizi_aif_api');

function fizi_aif_api() {
    $a = $_GET['aif_action'] ?? '';
    if (!in_array($a, ['health','create','list','get','update'])) return;

    header('Content-Type: application/json; charset=utf-8');
    header('Access-Control-Allow-Origin: *');

    global $wpdb;
    $t = $wpdb->prefix . 'ai_instant_task';

    if (!$wpdb->get_var("SHOW TABLES LIKE '$t'")) {
        $wpdb->query("CREATE TABLE $t (
            id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            parent_id INT UNSIGNED DEFAULT NULL,
            user_id VARCHAR(255) NOT NULL,
            prompt TEXT NOT NULL,
            url TEXT NOT NULL,
            page_url VARCHAR(500) DEFAULT NULL,
            path_files TEXT,
            status VARCHAR(50) DEFAULT 'task accepted',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
    }

    $cols = $wpdb->get_col("SHOW COLUMNS FROM $t");
    if (!in_array('parent_id', $cols)) $wpdb->query("ALTER TABLE $t ADD COLUMN parent_id INT UNSIGNED DEFAULT NULL AFTER id");
    if (!in_array('page_url', $cols)) $wpdb->query("ALTER TABLE $t ADD COLUMN page_url VARCHAR(500) DEFAULT NULL AFTER url");

    $raw = file_get_contents('php://input');
    $in  = json_decode($raw, true) ?: [];

    $valid_status = [
        'task accepted','task on going','task completed','task rejected',
        'dangerous_stop','complex_send','prompt_ask'
    ];

    if ($a === 'health') {
        echo json_encode(['status'=>'ok','server'=>'fizi-v7']); exit;
    }

// ── Forward task to the executor server ─────────────
function aif_forward_to_executor($task_id, $uid, $prompt, $url, $path_files = null) {
    $webhook = getenv('AIF_EXECUTOR_WEBHOOK') ?: getenv('AIF_HERMES_WEBHOOK') ?: '';
    $secret  = getenv('AIF_WEBHOOK_SECRET') ?: '';
    if (!$webhook || !$secret) return false; // fail closed

    $payload = json_encode([
        'user_id'        => $uid,
        'prompt'         => $prompt,
        'url'            => $url,
        'path_files'     => $path_files,
        'remote_task_id' => $task_id,
    ]);
    $signature = hash_hmac('sha256', $payload, $secret);
    $ch = curl_init($webhook);
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

    if ($a === 'create') {
        $uid      = trim($_GET['aif_user'] ?? $in['aif_user'] ?? $in['user_id'] ?? 'anonymous');
        $p        = trim($in['prompt'] ?? '');
        $url      = trim($in['url'] ?? '');
        $page_url = trim($in['page_url'] ?? $url);
        $parent   = $in['parent_id'] ? (int)$in['parent_id'] : null;
        if (!$p || !$url) { echo json_encode(['error'=>'prompt+url required']); exit; }

        $wpdb->query($wpdb->prepare(
            "INSERT INTO $t (parent_id,user_id,prompt,url,page_url,path_files,status) VALUES (%d,%s,%s,%s,%s,%s,%s)",
            $parent, $uid, $p, $url, $page_url, $in['path_files'] ?? '', 'task accepted'
        ));
        $new_id = $wpdb->insert_id;

        // Forward to the executor server to run the AI agent
        $forwarded = aif_forward_to_executor($new_id, $uid, $p, $url, $in['path_files'] ?? null);

        echo json_encode([
            'task_id'   => $new_id,
            'status'    => $forwarded ? 'task on going' : 'task accepted',
            'parent_id' => $parent,
            'warning'   => $forwarded ? null : 'Executor server unreachable',
        ]);
        exit;
    }

    if ($a === 'list') {
        $uid      = $_GET['aif_user'] ?? '';
        $st       = $_GET['status'] ?? '';
        $page_url = $_GET['page_url'] ?? '';
        $parent   = $_GET['parent_id'] ?? '';
        $lim      = min((int)($_GET['limit'] ?? 50), 200);

        $where = '1=1';
        if ($uid)      $where .= $wpdb->prepare(' AND user_id=%s', $uid);
        if ($st)       $where .= $wpdb->prepare(' AND status=%s', $st);
        if ($page_url) $where .= $wpdb->prepare(' AND page_url=%s', $page_url);
        if ($parent)   $where .= $wpdb->prepare(' AND (id=%d OR parent_id=%d)', $parent, $parent);

        $tasks = $wpdb->get_results(
            $wpdb->prepare("SELECT * FROM $t WHERE $where ORDER BY created_at DESC LIMIT %d", $lim), ARRAY_A
        );

        $cnt_where = '1=1';
        if ($uid)      $cnt_where .= $wpdb->prepare(' AND user_id=%s', $uid);
        if ($page_url) $cnt_where .= $wpdb->prepare(' AND page_url=%s', $page_url);
        $cnts = $wpdb->get_results("SELECT status, COUNT(*) c FROM $t WHERE $cnt_where GROUP BY status", ARRAY_A);
        $counts = []; foreach ($cnts as $r) $counts[$r['status']] = (int)$r['c'];

        echo json_encode(['tasks'=>$tasks?:[], 'counts'=>$counts]); exit;
    }

    if ($a === 'get') {
        $id = (int)($_GET['id'] ?? 0);
        $task = $wpdb->get_row($wpdb->prepare("SELECT * FROM $t WHERE id=%d", $id), ARRAY_A);
        $replies = $wpdb->get_results($wpdb->prepare("SELECT * FROM $t WHERE parent_id=%d ORDER BY created_at ASC", $id), ARRAY_A);
        $task['replies'] = $replies ?: [];
        echo json_encode($task ?: ['error'=>'not found']); exit;
    }

    if ($a === 'update') {
        $id = (int)($_GET['id'] ?? 0);
        $st = trim($in['status'] ?? '');
        if (!$id || !in_array($st, $valid_status)) { echo json_encode(['error'=>'invalid']); exit; }
        $wpdb->update($t, ['status'=>$st], ['id'=>$id]);
        echo json_encode(['task_id'=>$id, 'status'=>$st]); exit;
    }
}

// ── Admin menu ─────────────────────────────────────
add_action('admin_menu', function() {
    add_menu_page('Fizi AI Fix', 'Fizi AI Fix', 'manage_options', 'fizi-aif', function() {
        $api = home_url('/');
        echo '<div class="wrap"><h1>Fizi AI Instant Fix</h1>';
        echo '<p>Widget auto-injects for administrators and editors on the frontend.</p>';
        echo "<p>API endpoint: <code>{$api}?aif_action=</code></p>";
        echo '<hr><p><b>Status:</b> task accepted | task on going | task completed | task rejected</p>';
        echo '<p><b>Safety:</b> dangerous_stop | complex_send | prompt_ask</p>';
        echo '</div>';
    });
});

<?php
/**
 * AI Instant Fix — PHP Proxy
 * Forwards widget requests to the local Flask API server via Tailscale.
 * No WordPress dependency. Uses file_get_contents for forwarding.
 * 
 * The widget calls this file on same server (no CORS).
 * This file forwards to the local AI executor server (e.g. via Tailscale).
 */

// ── Config ─────────────────────────────────────────
$BACKEND = getenv('AIF_BACKEND') ?: 'http://localhost:5556';

// ── Headers ────────────────────────────────────────
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, PUT, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type, Authorization');
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { http_response_code(204); exit; }

// ── Route mapping ──────────────────────────────────
// Map query param actions to backend paths
$action = $_GET['action'] ?? '';

$routes = [
    'health'  => ['GET',  '/health'],
    'create'  => ['POST', '/api/tasks'],
    'list'    => ['GET',  '/api/tasks'],
    'get'     => ['GET',  '/api/tasks/' . intval($_GET['id'] ?? 0)],
    'update'  => ['PUT',  '/api/tasks/' . intval($_GET['id'] ?? 0)],
];

if (!isset($routes[$action])) {
    http_response_code(400);
    echo json_encode(['error' => 'Unknown action: ' . $action . '. Valid: health, create, list, get, update']);
    exit;
}

list($method, $path) = $routes[$action];

// Build query string (forward user_id, status, limit for list)
$query = '';
if ($action === 'list') {
    $params = [];
    if (!empty($_GET['user_id'])) $params[] = 'user_id=' . urlencode($_GET['user_id']);
    if (!empty($_GET['status']))  $params[] = 'status=' . urlencode($_GET['status']);
    if (!empty($_GET['limit']))   $params[] = 'limit=' . intval($_GET['limit']);
    if ($params) $query = '?' . implode('&', $params);
}

$url = $BACKEND . $path . $query;

// Forward request
$options = [
    'http' => [
        'method'  => $method,
        'header'  => "Content-Type: application/json\r\n",
        'timeout' => 30,
        'ignore_errors' => true,
    ],
];

// Pass request body for POST/PUT
if (in_array($method, ['POST', 'PUT'])) {
    $body = file_get_contents('php://input');
    $options['http']['content'] = $body;
}

$context = stream_context_create($options);
$response = @file_get_contents($url, false, $context);

// Extract status code from response headers
$status_code = 200;
if (isset($http_response_header)) {
    foreach ($http_response_header as $h) {
        if (preg_match('#HTTP/\d+\.\d+\s+(\d+)#', $h, $m)) {
            $status_code = intval($m[1]);
        }
    }
}

http_response_code($status_code ?: 502);

if ($response === false) {
    echo json_encode(['error' => 'Backend unreachable. Ensure the executor server is running.']);
} else {
    echo $response;
}

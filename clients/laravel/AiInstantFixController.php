<?php
/**
 * AI Instant Fix — Laravel variant
 *
 * A tiny proxy controller so your Laravel app's browser traffic talks to
 * YOUR domain (same-origin), and Laravel forwards to the AI Fix server
 * server-side. Keeps the AI Fix URL + token off the public frontend.
 *
 * Setup:
 *   1. Copy this controller into app/Http/Controllers/
 *   2. Add routes (see routes snippet below)
 *   3. Set env vars in .env:
 *        AIF_SERVER=https://fix.example.com
 *        AIF_TOKEN=*** JWT from POST /api/auth/login>
 *   4. Include the widget partial in your Blade layout.
 *
 * Routes (routes/web.php):
 *   use App\Http\Controllers\AiInstantFixController;
 *   Route::post('/ai-fix/tasks', [AiInstantFixController::class, 'createTask']);
 *   Route::get('/ai-fix/tasks', [AiInstantFixController::class, 'listTasks']);
 *   Route::get('/ai-fix/tasks/{id}', [AiInstantFixController::class, 'getTask'])
 *       ->whereNumber('id');
 *
 * Blade (resources/views/layout.blade.php) — before </body>:
 *   <script src="{{ asset('vendor/ai-instant-fix/ai-instant-fix.js') }}"
 *           data-aif-api="{{ url('/ai-fix') }}"
 *           data-aif-user-id="{{ auth()->id() ?? 'guest' }}"></script>
 *
 * NOTE: copy widget/ai-instant-fix.js into public/vendor/ai-instant-fix/
 * (or serve it from a CDN). The widget's POST /api/tasks path maps to
 * /ai-fix/api/tasks via the proxy below.
 */

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;

class AiInstantFixController extends Controller
{
    private function server(): string
    {
        return rtrim((string) env('AIF_SERVER'), '/');
    }

    private function headers(): array
    {
        $h = ['Content-Type' => 'application/json'];
        if ($token = env('AIF_TOKEN')) {
            $h['Authorization'] = 'Bearer ' . $token;
        }
        return $h;
    }

    public function createTask(Request $request)
    {
        $validated = $request->validate([
            'prompt' => 'required|string|max:5000',
            'url' => 'required|url|max:2000',
            'page_url' => 'nullable|url|max:2000',
            'parent_id' => 'nullable|integer|min:1',
        ]);

        $res = Http::withHeaders($this->headers())
            ->timeout(15)
            ->post($this->server() . '/api/tasks', array_merge($validated, [
                'user_id' => (string) ($request->user()->id ?? 'guest'),
            ]));

        return response()->json($res->json(), $res->status());
    }

    public function listTasks(Request $request)
    {
        $res = Http::withHeaders($this->headers())
            ->timeout(10)
            ->get($this->server() . '/api/tasks', array_filter([
                'page_url' => $request->query('page_url'),
                'user_id' => $request->query('user_id'),
                'status' => $request->query('status'),
                'limit' => min((int) $request->query('limit', 50), 200),
            ]));

        return response()->json($res->json(), $res->status());
    }

    public function getTask(int $id)
    {
        $res = Http::withHeaders($this->headers())
            ->timeout(10)
            ->get($this->server() . '/api/tasks/' . $id);

        return response()->json($res->json(), $res->status());
    }
}

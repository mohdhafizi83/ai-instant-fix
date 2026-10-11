"""Security headers, error hygiene, body-size cap, log hygiene."""
import logging


class TestSecurityHeaders:
    def test_api_response_headers(self, client):
        r = client.get('/health')
        assert r.headers['X-Content-Type-Options'] == 'nosniff'
        assert r.headers['X-Frame-Options'] == 'DENY'
        assert r.headers['Referrer-Policy'] == 'no-referrer'
        csp = r.headers['Content-Security-Policy']
        assert "default-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "base-uri 'none'" in csp
        assert "form-action 'none'" in csp

    def test_api_cache_control_no_store(self, client):
        r = client.get('/api/tasks')  # 401 still gets headers
        assert r.headers['Cache-Control'] == 'no-store'

    def test_widget_csp_allows_self(self, client):
        r = client.get('/widget/ai-instant-fix.js')
        assert r.status_code == 200
        csp = r.headers['Content-Security-Policy']
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_headers_on_error_responses(self, client):
        r = client.get('/nope')
        assert r.status_code == 404
        assert r.headers['X-Frame-Options'] == 'DENY'


class TestErrorHygiene:
    def test_404_is_generic_json(self, client):
        r = client.get('/definitely-not-a-route')
        assert r.status_code == 404
        assert r.get_json() == {'error': 'not found'}
        assert b'Traceback' not in r.data

    def test_405_is_generic_json(self, client):
        r = client.delete('/health')
        assert r.status_code == 405
        assert r.get_json() == {'error': 'method not allowed'}

    def test_unhandled_exception_leaks_nothing(self, client, isolated_env):
        """A route that raises returns generic JSON 500; traceback only logged."""
        @isolated_env.app.route('/tests/boom')
        def _boom():
            raise RuntimeError('super-secret-internal-detail')

        r = client.get('/tests/boom')
        assert r.status_code == 500
        assert r.get_json() == {'error': 'internal server error'}
        assert b'super-secret-internal-detail' not in r.data
        assert b'Traceback' not in r.data


class TestBodySizeCap:
    def test_oversize_body_rejected_413(self, client, monkeypatch):
        monkeypatch.setenv('DEV_ALLOW_ANON', '1')
        big = {'prompt': 'x' * (2 * 1024 * 1024), 'url': 'https://e.com'}
        r = client.post('/api/tasks', json=big)
        assert r.status_code == 413
        assert r.get_json() == {'error': 'request entity too large'}

    def test_normal_body_accepted(self, client, monkeypatch):
        monkeypatch.setenv('DEV_ALLOW_ANON', '1')
        monkeypatch.setattr('app.spawn_executor', lambda *a, **k: True)
        monkeypatch.setattr('app.sync_to_power_tool', lambda *a, **k: None)
        r = client.post('/api/tasks',
                        json={'prompt': 'fix the header', 'url': 'https://e.com'})
        assert r.status_code == 201


class TestLogHygiene:
    def test_bearer_token_never_logged(self, client, make_jwt, monkeypatch, caplog):
        """Authorization header value must not appear in any log record."""
        monkeypatch.setenv('JWT_SECRET', 'test-secret-0123456789abcdef0123456789abcdef')
        token = make_jwt(secret='test-secret-0123456789abcdef0123456789abcdef')
        with caplog.at_level(logging.DEBUG):
            client.get('/api/tasks', headers={'Authorization': f'Bearer {token}'})
            client.get('/api/tasks', headers={'Authorization': 'Bearer bad-token'})
        logged = '\n'.join(rec.getMessage() for rec in caplog.records)
        assert token not in logged
        assert 'bad-token' not in logged

    def test_webhook_secret_never_logged(self, client, monkeypatch, caplog):
        monkeypatch.setenv('WEBHOOK_SECRET', 'topsecret-hmac-key')
        with caplog.at_level(logging.DEBUG):
            client.post('/api/webhook/task', json={'prompt': 'x', 'url': 'y'},
                        headers={'X-AIF-Signature': 'wrong'})
        logged = '\n'.join(rec.getMessage() for rec in caplog.records)
        assert 'topsecret-hmac-key' not in logged


class TestExistingBehaviour:
    """Response-shape regression guards for the preserved endpoints."""

    def test_health_shape(self, client):
        r = client.get('/health')
        assert r.status_code == 200
        assert r.get_json() == {'status': 'ok'}

    def test_settings_shape(self, client):
        r = client.get('/api/settings')
        assert r.status_code == 200
        body = r.get_json()
        assert set(body) == {'api_url', 'executor_profile', 'auth_required'}

    def test_widget_whitelist(self, client):
        assert client.get('/widget/../../etc/passwd').status_code in (404, 400)
        assert client.get('/widget/nope.js').status_code == 404

    def test_task_crud_roundtrip(self, client, monkeypatch):
        monkeypatch.setenv('DEV_ALLOW_ANON', '1')
        monkeypatch.setattr('app.spawn_executor', lambda *a, **k: True)
        monkeypatch.setattr('app.sync_to_power_tool', lambda *a, **k: None)
        r = client.post('/api/tasks',
                        json={'prompt': 'fix the header', 'url': 'https://e.com'})
        assert r.status_code == 201
        tid = r.get_json()['task_id']
        r2 = client.get(f'/api/tasks/{tid}')
        assert r2.status_code == 200
        assert r2.get_json()['prompt'] == 'fix the header'
        r3 = client.get('/api/tasks')
        assert r3.status_code == 200
        assert 'tasks' in r3.get_json() and 'counts' in r3.get_json()

    def test_poll_501_when_unconfigured(self, client, monkeypatch):
        monkeypatch.setenv('DEV_ALLOW_ANON', '1')
        r = client.get('/api/poll')
        assert r.status_code == 501

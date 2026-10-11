"""Auth behaviour: fail-closed JWT, DEV_ALLOW_ANON escape hatch, webhook HMAC."""
import hashlib
import hmac

TASK = {'prompt': 'fix the header color', 'url': 'https://example.com/page'}


def _sign(secret, body_bytes):
    return hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()


class TestJwtFailClosed:
    def test_no_jwt_secret_blocks_anonymous(self, client):
        """JWT_SECRET unset → protected routes return 401 (fail closed)."""
        r = client.get('/api/tasks')
        assert r.status_code == 401
        assert 'error' in r.get_json()

    def test_no_jwt_secret_blocks_post(self, client):
        r = client.post('/api/tasks', json=TASK)
        assert r.status_code == 401

    def test_dev_allow_anon_enables_local_dev(self, client, monkeypatch):
        monkeypatch.setenv('DEV_ALLOW_ANON', '1')
        r = client.get('/api/tasks')
        assert r.status_code == 200

    def test_dev_allow_anon_only_when_exactly_1(self, client, monkeypatch):
        monkeypatch.setenv('DEV_ALLOW_ANON', 'yes')
        assert client.get('/api/tasks').status_code == 401

    def test_valid_jwt_accepted(self, client, make_jwt, monkeypatch):
        monkeypatch.setenv('JWT_SECRET', 'test-secret-0123456789abcdef0123456789abcdef')
        r = client.get('/api/tasks', headers={'Authorization': f'Bearer {make_jwt()}'})
        assert r.status_code == 200

    def test_missing_header_rejected(self, client, monkeypatch):
        monkeypatch.setenv('JWT_SECRET', 'test-secret-0123456789abcdef0123456789abcdef')
        assert client.get('/api/tasks').status_code == 401

    def test_wrong_secret_rejected(self, client, make_jwt, monkeypatch):
        monkeypatch.setenv('JWT_SECRET', 'real-secret-0123456789abcdef0123456789abcd')
        token = make_jwt(secret='attacker-secret-0123456789abcdef01234567')
        r = client.get('/api/tasks', headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 401

    def test_garbage_token_rejected(self, client, monkeypatch):
        monkeypatch.setenv('JWT_SECRET', 'test-secret-0123456789abcdef0123456789abcdef')
        r = client.get('/api/tasks', headers={'Authorization': 'Bearer not.a.jwt'})
        assert r.status_code == 401


class TestLogin:
    def test_login_501_when_unconfigured(self, client):
        r = client.post('/api/auth/login', json={'password': 'x'})
        assert r.status_code == 501

    def test_login_issues_working_token(self, client, monkeypatch):
        monkeypatch.setenv('JWT_SECRET', 'test-secret-0123456789abcdef0123456789abcdef')
        monkeypatch.setenv('ADMIN_PASSWORD', 'hunter2')
        r = client.post('/api/auth/login', json={'password': 'hunter2'})
        assert r.status_code == 200
        token = r.get_json()['token']
        r2 = client.get('/api/tasks', headers={'Authorization': f'Bearer {token}'})
        assert r2.status_code == 200

    def test_login_wrong_password(self, client, monkeypatch):
        monkeypatch.setenv('JWT_SECRET', 'test-secret-0123456789abcdef0123456789abcdef')
        monkeypatch.setenv('ADMIN_PASSWORD', 'hunter2')
        assert client.post('/api/auth/login', json={'password': 'nope'}).status_code == 401


class TestWebhookSignature:
    def test_bad_signature_rejected(self, client, monkeypatch):
        monkeypatch.setenv('WEBHOOK_SECRET', 'wh-secret-0123456789abcdef0123456789abcd')
        r = client.post('/api/webhook/task', json=TASK,
                        headers={'X-AIF-Signature': 'deadbeef'})
        assert r.status_code == 401

    def test_missing_signature_rejected(self, client, monkeypatch):
        monkeypatch.setenv('WEBHOOK_SECRET', 'wh-secret-0123456789abcdef0123456789abcd')
        r = client.post('/api/webhook/task', json=TASK)
        assert r.status_code == 401

    def test_webhook_disabled_without_secret(self, client):
        """WEBHOOK_SECRET unset → webhook fails closed even with a 'valid' sig."""
        r = client.post('/api/webhook/task', json=TASK,
                        headers={'X-AIF-Signature': 'anything'})
        assert r.status_code == 401

    def test_valid_signature_accepted(self, client, monkeypatch):
        monkeypatch.setenv('WEBHOOK_SECRET', 'wh-secret-0123456789abcdef0123456789abcd')
        # Avoid outbound network calls from the task pipeline.
        monkeypatch.setattr('app.spawn_executor', lambda *a, **k: True)
        monkeypatch.setattr('app.sync_to_power_tool', lambda *a, **k: None)
        body = b'{"prompt": "fix the header color", "url": "https://example.com/page"}'
        sig = _sign('wh-secret-0123456789abcdef0123456789abcd', body)
        r = client.post('/api/webhook/task', data=body,
                        headers={'X-AIF-Signature': sig,
                                 'Content-Type': 'application/json'})
        assert r.status_code == 201
        assert r.get_json()['status'] == 'task on going'


class TestTaskMutationAuth:
    def test_put_requires_auth(self, client, monkeypatch):
        monkeypatch.setenv('JWT_SECRET', 'test-secret-0123456789abcdef0123456789abcdef')
        r = client.put('/api/tasks/1', json={'status': 'task completed'})
        assert r.status_code == 401

    def test_put_with_valid_jwt(self, client, make_jwt, monkeypatch):
        monkeypatch.setenv('JWT_SECRET', 'test-secret-0123456789abcdef0123456789abcdef')
        import db as db_mod
        task_id = db_mod.create_task('u1', 'p', 'https://e.com')
        r = client.put(f'/api/tasks/{task_id}', json={'status': 'task completed'},
                       headers={'Authorization': f'Bearer {make_jwt()}'})
        assert r.status_code == 200
        assert r.get_json()['status'] == 'task completed'

    def test_put_with_valid_webhook_sig(self, client, monkeypatch):
        monkeypatch.setenv('WEBHOOK_SECRET', 'wh-secret-0123456789abcdef0123456789abcd')
        import db as db_mod
        task_id = db_mod.create_task('u1', 'p', 'https://e.com')
        body = b'{"status": "task completed"}'
        sig = _sign('wh-secret-0123456789abcdef0123456789abcd', body)
        r = client.put(f'/api/tasks/{task_id}', data=body,
                       headers={'X-AIF-Signature': sig,
                                'Content-Type': 'application/json'})
        assert r.status_code == 200

    def test_put_fail_closed_when_nothing_configured(self, client):
        import db as db_mod
        task_id = db_mod.create_task('u1', 'p', 'https://e.com')
        r = client.put(f'/api/tasks/{task_id}', json={'status': 'task completed'})
        assert r.status_code == 401

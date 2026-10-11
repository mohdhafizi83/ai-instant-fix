"""Shared pytest fixtures for the AI Instant Fix server test suite."""
import os
import sys

import pytest

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Fresh DB + clean auth-related env for every test."""
    monkeypatch.setenv('AIF_DB', str(tmp_path / 'test.db'))
    for var in ('JWT_SECRET', 'WEBHOOK_SECRET', 'DEV_ALLOW_ANON',
                'ADMIN_PASSWORD', 'PHP_API_URL', 'POWER_TOOL_URL',
                'POWER_TOOL_CLIENT_ID', 'EXECUTOR_CMD',
                'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID'):
        monkeypatch.delenv(var, raising=False)

    import db
    importlib_reload(db)
    db.init_db()

    import app as app_module
    importlib_reload(app_module)
    app_module.app.config['TESTING'] = True
    yield app_module


def importlib_reload(module):
    import importlib
    importlib.reload(module)


@pytest.fixture
def client(isolated_env):
    return isolated_env.app.test_client()


@pytest.fixture
def make_jwt(isolated_env):
    """Factory: mint a HS256 token signed with the given secret."""
    import jwt as _jwt
    import time as _time

    def _make(secret='test-secret-0123456789abcdef0123456789abcdef', **claims):
        payload = {'sub': 'admin', 'iat': int(_time.time())}
        payload.update(claims)
        return _jwt.encode(payload, secret, algorithm='HS256')
    return _make

import os
from tempfile import TemporaryDirectory
from unittest.mock import patch


def test_login_guards():
    with TemporaryDirectory() as d:
        env = {
            'MONITOR_SKIP_AUTOSTART': '1',
            'MONITOR_USER': 'admin',
            'MONITOR_PASSWORD': 'a-really-long-password',
            'FLASK_SECRET_KEY': 'secret-key-that-is-at-least-32-long!!',
            'COOKIE_SECURE': '0',
            'MONITOR_DB': d + '/db.sqlite3',
        }
        with patch.dict(os.environ, env):
            from app import create_app
            app = create_app(start_sampler=False)
            client = app.test_client()
            assert client.get('/').status_code == 302
            assert client.get('/api/live').status_code == 401
            assert client.get('/healthz').json == {'status': 'ok'}
            assert client.post('/login', data={'username': 'admin', 'password': 'wrong'}).status_code == 200
            assert client.post('/login', data={'username': 'admin', 'password': env['MONITOR_PASSWORD']}).status_code == 302
            assert client.get('/').status_code == 200
            assert client.get('/api/history?range=bad').status_code == 400
            assert client.get('/api/live').status_code == 503
            assert client.post('/logout').status_code == 302
            assert client.get('/').status_code == 302

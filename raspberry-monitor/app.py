"""Authenticated monitoring web application; one Gunicorn worker owns the sampler."""
from __future__ import annotations

import hmac
import os
import threading
import time
from pathlib import Path

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

from monitor import Collector, active_alerts
from storage import events, history, init_db, record


def create_app(start_sampler=True):
    username = os.environ.get('MONITOR_USER', '').strip()
    password = os.environ.get('MONITOR_PASSWORD', '')
    secret = os.environ.get('FLASK_SECRET_KEY', '')
    if (not username or len(password) < 12 or len(secret) < 32
            or password.startswith('REPLACE_') or secret.startswith('REPLACE_')):
        raise RuntimeError('Nastav MONITOR_USER, MONITOR_PASSWORD (min. 12) a FLASK_SECRET_KEY (min. 32).')

    application = Flask(__name__)
    application.secret_key = secret
    application.config.update(
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', '1') == '1',
        PERMANENT_SESSION_LIFETIME=12 * 3600,
        MAX_CONTENT_LENGTH=4096,
    )
    # Only a local reverse proxy is expected to set forwarding headers.
    if os.environ.get('TRUST_PROXY', '0') == '1':
        application.wsgi_app = ProxyFix(application.wsgi_app, x_for=1, x_proto=1)
    db_path = os.getenv('MONITOR_DB', '/var/lib/raspberry-monitor/metrics.sqlite3')
    init_db(db_path)
    state = {'sample': None, 'alerts': [], 'updated_at': 0}
    state_lock = threading.Lock()
    login_attempts = {}
    login_lock = threading.Lock()
    interval = max(10, int(os.environ.get('SAMPLE_INTERVAL', '20')))

    def sampler():
        collector = Collector()
        while True:
            try:
                snapshot = collector.take()
                warnings = active_alerts(snapshot)
                record(db_path, snapshot, warnings)
                with state_lock:
                    state.update(sample=snapshot, alerts=warnings, updated_at=snapshot['ts'])
            except Exception:
                application.logger.exception('Sběr metrik selhal')
            time.sleep(interval)

    if start_sampler:
        threading.Thread(target=sampler, name='metric-sampler', daemon=True).start()

    @application.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @application.before_request
    def protect():
        if request.path in ('/login', '/healthz') or request.path.startswith('/static/'):
            return None
        if not session.get('authenticated'):
            if request.path.startswith('/api/'):
                abort(401)
            return redirect(url_for('login'))
        return None

    @application.get('/healthz')
    def healthz():
        return jsonify(status='ok')

    @application.route('/login', methods=['GET', 'POST'])
    def login():
        if session.get('authenticated'):
            return redirect(url_for('index'))
        error = False
        if request.method == 'POST':
            ip = request.remote_addr or 'unknown'
            now = time.monotonic()
            with login_lock:
                recent = [t for t in login_attempts.get(ip, []) if now - t < 600]
                if len(recent) >= 8:
                    abort(429)
                login_attempts[ip] = recent
            user_ok = hmac.compare_digest(request.form.get('username', ''), username)
            pass_ok = hmac.compare_digest(request.form.get('password', ''), password)
            if user_ok and pass_ok:
                with login_lock:
                    login_attempts.pop(ip, None)
                session.clear()
                session['authenticated'] = True
                session.permanent = True
                return redirect(url_for('index'))
            with login_lock:
                login_attempts[ip].append(now)
            error = True
        return render_template('login.html', error=error)

    @application.post('/logout')
    def logout():
        # POST + same-site session cookie; no mutating system actions exist.
        session.clear()
        return redirect(url_for('login'))

    @application.get('/')
    def index():
        return render_template('index.html')

    @application.get('/api/live')
    def live():
        with state_lock:
            sample, alerts = state['sample'], state['alerts']
        if not sample:
            return jsonify(error='Čekám na první měření'), 503
        return jsonify(sample=sample, alerts=alerts,
                       recent_events=events(db_path, 8),
                       refresh_interval=interval)

    @application.get('/api/history')
    def historical():
        ranges = {'1h': 3600, '24h': 86400, '7d': 7 * 86400}
        key = request.args.get('range', '24h')
        if key not in ranges:
            return jsonify(error='Nepodporovaný interval'), 400
        return jsonify(range=key, points=history(db_path, ranges[key]))

    return application


if os.getenv('MONITOR_SKIP_AUTOSTART', '0') == '1':
    # Testing may import create_app without starting the background worker.
    app = None
else:
    app = create_app()

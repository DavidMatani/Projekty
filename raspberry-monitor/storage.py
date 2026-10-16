"""Bounded SQLite telemetry, history aggregation and alert state changes."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


def connect(db_path):
    connection = sqlite3.connect(db_path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA busy_timeout=5000')
    return connection


def init_db(db_path):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS samples (
              ts INTEGER PRIMARY KEY,
              cpu REAL NOT NULL,
              ram REAL NOT NULL,
              temperature REAL,
              load1 REAL NOT NULL,
              net_down REAL NOT NULL,
              net_up REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alert_state (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              active INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              title TEXT NOT NULL,
              detail TEXT NOT NULL,
              active INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
        ''')


def record(db_path, sample, alerts):
    now = sample['ts']
    present = {a['id']: a for a in alerts}
    with connect(db_path) as conn:
        conn.execute('''INSERT OR REPLACE INTO samples
            (ts, cpu, ram, temperature, load1, net_down, net_up)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (now, sample['cpu']['percent'], sample['memory']['percent'],
             sample['temperature'], sample['cpu']['load'][0],
             sample['network']['down'], sample['network']['up']))
        states = {row['id']: row for row in conn.execute('SELECT * FROM alert_state')}
        for key, alert in present.items():
            if key not in states or not states[key]['active']:
                conn.execute('INSERT INTO events(ts, title, detail, active) VALUES(?,?,?,1)',
                             (now, alert['title'], alert['detail']))
            conn.execute('''INSERT INTO alert_state(id, title, active) VALUES(?,?,1)
                            ON CONFLICT(id) DO UPDATE SET title=excluded.title, active=1''',
                         (key, alert['title']))
        for key, old in states.items():
            if old['active'] and key not in present:
                conn.execute('UPDATE alert_state SET active=0 WHERE id=?', (key,))
                conn.execute('INSERT INTO events(ts,title,detail,active) VALUES(?,?,?,0)',
                             (now, old['title'], 'Hodnota se vrátila do normálu'))
        conn.execute('DELETE FROM samples WHERE ts < ?', (now - 8 * 86400,))
        conn.execute('DELETE FROM events WHERE ts < ?', (now - 30 * 86400,))


def history(db_path, duration_seconds, max_points=280):
    start = int(time.time()) - duration_seconds
    bucket = max(15, duration_seconds // max_points)
    with connect(db_path) as conn:
        rows = conn.execute('''
            SELECT CAST((ts-?)/? AS INTEGER) AS slot, MAX(ts) ts,
                   ROUND(AVG(cpu),1) cpu, ROUND(AVG(ram),1) ram,
                   ROUND(AVG(temperature),1) temperature,
                   ROUND(AVG(load1),2) load1,
                   ROUND(AVG(net_down),1) net_down,
                   ROUND(AVG(net_up),1) net_up
            FROM samples WHERE ts >= ?
            GROUP BY slot ORDER BY slot ASC
        ''', (start, bucket, start)).fetchall()
    return [dict(row) for row in rows]


def events(db_path, limit=20):
    with connect(db_path) as conn:
        return [dict(row) for row in conn.execute(
            'SELECT ts, title, detail, active FROM events ORDER BY id DESC LIMIT ?', (limit,))]

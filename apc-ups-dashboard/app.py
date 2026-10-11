from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from functools import wraps
from pathlib import Path

from flask import Flask, flash, jsonify, redirect, render_template_string, request, session, url_for
from werkzeug.security import check_password_hash

APP_VERSION = "2.5-docker"
APC = shutil.which("apcaccess") or "/usr/sbin/apcaccess"
APC_HOST = os.environ.get("UPS_APC_HOST", "127.0.0.1:3551").strip()
SETTINGS_SOCKET = os.environ.get("UPS_SETTINGS_SOCKET", "/run/apc-ups-dashboard/settings.sock")
BRIDGE_TOKEN = os.environ.get("UPS_BRIDGE_TOKEN", "").strip()
DATA_ROOT = Path(os.environ.get("UPS_DASHBOARD_DATA", "/data"))
DB_PATH = DATA_ROOT / "history.sqlite3"
POLL_SECONDS = max(10, int(os.environ.get("UPS_POLL_SECONDS", "30")))
RETENTION_DAYS = max(1, int(os.environ.get("UPS_RETENTION_DAYS", "30")))
ADMIN_USER = os.environ.get("UPS_ADMIN_USER", "admin").strip() or "admin"
ADMIN_PASSWORD_HASH = os.environ.get("UPS_ADMIN_PASSWORD_HASH", "").strip()
ADMIN_SESSION_MINUTES = max(5, int(os.environ.get("UPS_ADMIN_SESSION_MINUTES", "30")))
SECRET_KEY = os.environ.get("UPS_DASHBOARD_SECRET_KEY", "").strip() or secrets.token_hex(32)

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
    PERMANENT_SESSION_LIFETIME=dt.timedelta(minutes=ADMIN_SESSION_MINUTES),
)

DATA_ROOT.mkdir(parents=True, exist_ok=True)
_collector_started = False
_collector_lock = threading.Lock()


def num(value):
    m = re.search(r"[-+]?\d+(?:\.\d+)?", value or "")
    return float(m.group()) if m else None


def read_apc() -> dict[str, str]:
    cmd = [APC, "status"]
    if APC_HOST:
        cmd.append(APC_HOST)
    p = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=8)
    raw: dict[str, str] = {}
    for line in p.stdout.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            raw[key.strip()] = value.strip()
    return raw


def read_events(limit=120) -> list[str]:
    try:
        response = bridge_request("events", limit=max(1, min(int(limit), 500)))
        return response.get("events", [])
    except Exception:
        return []


def parse_battery_date(value: str | None):
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def battery_condition(raw: dict[str, str]) -> dict:
    """Conservative battery-health assessment.

    APC Back-UPS does not expose a true SOH percentage. We therefore report a
    qualitative state from explicit UPS alarms/self-test plus battery age.
    Charge percentage is intentionally not treated as battery health.
    """
    status = (raw.get("STATUS") or "").upper()
    selftest = (raw.get("SELFTEST") or "").strip()
    selftest_upper = selftest.upper()
    installed = parse_battery_date(raw.get("BATTDATE"))
    age_years = None
    if installed:
        age_years = max(0.0, (dt.date.today() - installed).days / 365.2425)

    if "REPLACEBATT" in status or "NOBATT" in status:
        return {
            "label": "VYMĚNIT",
            "code": "replace",
            "reason": "UPS přímo hlásí problém s baterií nebo požadavek na její výměnu.",
            "age_years": round(age_years, 1) if age_years is not None else None,
        }

    bad_selftest_tokens = ("FAIL", "FAILED", "BAD", "NG")
    if any(token in selftest_upper for token in bad_selftest_tokens):
        return {
            "label": "VYMĚNIT",
            "code": "replace",
            "reason": f"Poslední self-test baterie není v pořádku ({selftest}).",
            "age_years": round(age_years, 1) if age_years is not None else None,
        }

    # Age is only a warning heuristic, not a measured state-of-health value.
    if age_years is not None and age_years >= 4.0:
        return {
            "label": "SLÁBNE",
            "code": "weak",
            "reason": f"Baterie je stará přibližně {age_years:.1f} roku. UPS zatím nehlásí nutnost výměny.",
            "age_years": round(age_years, 1),
        }

    if selftest_upper in {"OK", "PASSED", "PASS"}:
        reason = "Poslední self-test je v pořádku a UPS nehlásí požadavek na výměnu."
        if age_years is not None:
            reason += f" Stáří baterie je přibližně {age_years:.1f} roku."
        return {
            "label": "DOBRÁ",
            "code": "good",
            "reason": reason,
            "age_years": round(age_years, 1) if age_years is not None else None,
        }

    if age_years is not None and age_years < 4.0:
        return {
            "label": "DOBRÁ",
            "code": "good",
            "reason": "UPS nehlásí problém s baterií. Kondice je orientační, protože není k dispozici jednoznačný výsledek self-testu.",
            "age_years": round(age_years, 1),
        }

    return {
        "label": "NEZNÁMÁ",
        "code": "unknown",
        "reason": "UPS nehlásí přesné SOH baterie a nemáme dost údajů pro spolehlivé hodnocení.",
        "age_years": None,
    }


def status_data() -> dict:
    raw = read_apc()
    load = num(raw.get("LOADPCT"))
    power = num(raw.get("NOMPOWER"))
    status = raw.get("STATUS", "UNKNOWN")
    health = battery_condition(raw)
    return {
        "model": raw.get("MODEL", "APC UPS"),
        "name": raw.get("UPSNAME", "UPS"),
        "status": status,
        "online": "ONLINE" in status,
        "on_battery": "ONBATT" in status,
        "low_battery": "LOWBATT" in status,
        "battery": num(raw.get("BCHARGE")),
        "runtime": num(raw.get("TIMELEFT")),
        "load": load,
        "watts": round(load * power / 100) if load is not None and power else None,
        "power": power,
        "linev": num(raw.get("LINEV")),
        "battv": num(raw.get("BATTV")),
        "nombattv": num(raw.get("NOMBATTV")),
        "nominv": num(raw.get("NOMINV")),
        "lowtrans": num(raw.get("LOTRANS")),
        "hitrans": num(raw.get("HITRANS")),
        "sense": raw.get("SENSE"),
        "alarmdel": raw.get("ALARMDEL"),
        "battery_date": raw.get("BATTDATE"),
        "battery_condition": health["label"],
        "battery_condition_code": health["code"],
        "battery_condition_reason": health["reason"],
        "battery_age_years": health["age_years"],
        "last_transfer": raw.get("LASTXFER"),
        "num_transfers": num(raw.get("NUMXFERS")),
        "time_on_battery": num(raw.get("TONBATT")),
        "cumulative_on_battery": num(raw.get("CUMONBATT")),
        "off_battery": raw.get("XOFFBATT"),
        "selftest": raw.get("SELFTEST"),
        "min_batt": num(raw.get("MBATTCHG")),
        "min_time": num(raw.get("MINTIMEL")),
        "max_time": num(raw.get("MAXTIME")),
        "serial": raw.get("SERIALNO"),
        "firmware": raw.get("FIRMWARE"),
        "cable": raw.get("CABLE"),
        "driver": raw.get("DRIVER"),
        "mode": raw.get("UPSMODE"),
        "started": raw.get("STARTTIME"),
        "date": raw.get("DATE"),
        "raw": raw,
    }


def db_connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=10000")
    return db


def init_db():
    with contextlib.closing(db_connect()) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS samples (
                ts INTEGER PRIMARY KEY,
                status TEXT,
                linev REAL,
                battery REAL,
                runtime REAL,
                load REAL,
                watts REAL,
                battv REAL
            );
            CREATE TABLE IF NOT EXISTS outages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_ts INTEGER NOT NULL,
                ended_ts INTEGER,
                start_battery REAL,
                end_battery REAL,
                reason TEXT
            );
            """
        )
        db.commit()


def sample_once():
    data = status_data()
    now = int(time.time())
    with contextlib.closing(db_connect()) as db:
        previous = db.execute("SELECT status,battery FROM samples ORDER BY ts DESC LIMIT 1").fetchone()
        db.execute(
            "INSERT OR REPLACE INTO samples(ts,status,linev,battery,runtime,load,watts,battv) VALUES(?,?,?,?,?,?,?,?)",
            (now, data["status"], data["linev"], data["battery"], data["runtime"], data["load"], data["watts"], data["battv"]),
        )
        prev_on = previous is not None and "ONBATT" in (previous["status"] or "")
        now_on = data["on_battery"]
        if now_on and not prev_on:
            db.execute(
                "INSERT INTO outages(started_ts,start_battery,reason) VALUES(?,?,?)",
                (now, data["battery"], data["last_transfer"]),
            )
        elif prev_on and not now_on:
            row = db.execute("SELECT id FROM outages WHERE ended_ts IS NULL ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE outages SET ended_ts=?,end_battery=? WHERE id=?", (now, data["battery"], row["id"]))
        cutoff = now - RETENTION_DAYS * 86400
        db.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        db.commit()


def collector():
    while True:
        try:
            sample_once()
        except Exception:
            app.logger.exception("UPS history sampling failed")
        time.sleep(POLL_SECONDS)


def start_collector():
    global _collector_started
    with _collector_lock:
        if _collector_started:
            return
        init_db()
        threading.Thread(target=collector, name="ups-history", daemon=True).start()
        _collector_started = True


start_collector()


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def csrf_token():
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def require_csrf():
    expected = session.get("csrf", "")
    supplied = request.form.get("csrf", "")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise ValueError("Neplatný bezpečnostní token.")


def read_config_values() -> dict[str, str]:
    try:
        response = bridge_request("get_config")
        values = response.get("config", {})
        return values if isinstance(values, dict) else {}
    except Exception:
        return {}


def bridge_request(action: str, **payload_fields) -> dict:
    if not BRIDGE_TOKEN:
        raise RuntimeError("Chybí UPS_BRIDGE_TOKEN.")
    request_data = {"token": BRIDGE_TOKEN, "action": action, **payload_fields}
    payload = (json.dumps(request_data, separators=(",", ":")) + "\n").encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(70)
        sock.connect(SETTINGS_SOCKET)
        sock.sendall(payload)
        chunks = []
        total = 0
        while total <= 262144:
            part = sock.recv(4096)
            if not part:
                break
            chunks.append(part)
            total += len(part)
            if b"\n" in part:
                break
    if not chunks:
        raise RuntimeError("Pomocná služba UPS neodpověděla.")
    response = json.loads(b"".join(chunks).split(b"\n", 1)[0].decode("utf-8"))
    if not response.get("ok"):
        raise RuntimeError(response.get("error") or "Požadavek na UPS se nepodařil.")
    return response


def bridge_apply(changes: dict[str, str]) -> None:
    bridge_request("apply", changes=changes)


PAGE = r'''<!doctype html><html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>APC UPS</title>
<style>
:root{color-scheme:dark;--bg:#07101f;--card:#121f35;--card2:#0f1b2e;--muted:#93a4bd;--green:#43d17d;--yellow:#f6c453;--red:#ff6b75;--blue:#5aa7ff}*{box-sizing:border-box}body{margin:0;font-family:system-ui;background:radial-gradient(circle at 20% 0,#17365d 0,transparent 32rem),var(--bg);color:#f5f7fb}.wrap{max-width:1180px;margin:auto;padding:26px 16px 50px}header{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:18px}h1,h2{margin:0}.sub,.label{color:var(--muted)}.pill,.btn{padding:10px 14px;border-radius:99px;background:#1b2b46;font-weight:800;border:1px solid #ffffff16;color:#fff;text-decoration:none}.pill.online{color:var(--green)}.pill.batt{color:var(--yellow)}.pill.bad{color:var(--red)}.health.good{color:var(--green)}.health.weak{color:var(--yellow)}.health.replace{color:var(--red)}.health.unknown{color:var(--muted)}.health-note{display:block;margin-top:5px;color:var(--muted);font-size:12px;line-height:1.35}.grid{display:grid;grid-template-columns:1.2fr .8fr;gap:15px}.card{background:linear-gradient(180deg,#162642,var(--card));border:1px solid #ffffff12;border-radius:19px;padding:20px}.big{font-size:32px;font-weight:800}.row{display:flex;justify-content:space-between;gap:15px;align-items:center}.battery,.bar{height:24px;border-radius:8px;background:#ffffff12;overflow:hidden;margin:18px 0}.battery i,.bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--green),#55d6e8);transition:.3s}.bar i{background:var(--blue)}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:#ffffff12;border-radius:14px;overflow:hidden}.stat{background:var(--card2);padding:14px;min-width:0}.stat b{display:block;margin-top:5px;font-size:17px;overflow-wrap:anywhere}.full{grid-column:1/-1}.section{margin-top:15px}.table{width:100%;border-collapse:collapse}.table td{padding:9px 8px;border-bottom:1px solid #ffffff10;vertical-align:top}.table td:first-child{color:var(--muted);width:42%}.history-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}.history-card{background:#0b1628;border:1px solid #ffffff10;border-radius:14px;padding:14px;min-width:0}.history-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.history-title{font-size:18px;font-weight:800}.history-unit{color:var(--muted);font-size:13px}.history-stats{display:flex;gap:12px;flex-wrap:wrap;font-size:13px;color:var(--muted)}.history-stats b{color:#fff}.chart-wrap{position:relative;margin-top:10px}.chart{width:100%;height:220px;display:block;border-radius:10px}.chart-detail{min-height:22px;margin-top:7px;color:#dce7f6;font-size:13px;font-variant-numeric:tabular-nums}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.tabs .btn.active{background:#285ca8;border-color:#6aa7ff}.history-note{margin-top:10px}.small{font-size:13px}.error{margin-top:15px;padding:12px;border-radius:12px;background:#5a2229;display:none}.events{max-height:340px;overflow:auto}.event{padding:9px 0;border-bottom:1px solid #ffffff10;font-family:ui-monospace,monospace;font-size:13px}.outage{display:grid;grid-template-columns:1.4fr 1fr 1fr 1.3fr;gap:8px;padding:10px 0;border-bottom:1px solid #ffffff10}.topactions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}@media(max-width:760px){.grid{grid-template-columns:1fr}.full{grid-column:auto}header{align-items:flex-start;flex-direction:column}.stats{grid-template-columns:1fr 1fr}.outage{grid-template-columns:1fr 1fr}.history-grid{grid-template-columns:1fr}.chart{height:200px}}
</style></head><body><main class="wrap"><header><div><div class="sub">NAPÁJENÍ SERVERU</div><h1 id="model">APC UPS</h1><div class="sub" id="name">Načítám…</div></div><div class="topactions"><a class="btn" href="/admin">Nastavení</a><div class="pill" id="pill">NAČÍTÁM</div></div></header>
<section class="grid"><article class="card"><div class="row"><div><div class="label">Baterie</div><div class="big" id="battery">—</div></div><div><div class="label">Výdrž</div><div class="big" id="runtime">—</div></div></div><div class="battery"><i id="bfill"></i></div><div class="stats"><div class="stat"><span class="label">Napětí baterie</span><b id="battv">—</b></div><div class="stat"><span class="label">Nominální</span><b id="nombattv">—</b></div><div class="stat"><span class="label">Datum baterie</span><b id="bdate">—</b></div><div class="stat"><span class="label">Kondice baterie</span><b id="health" class="health unknown">—</b><span class="health-note" id="healthreason">—</span></div><div class="stat"><span class="label">Stáří baterie</span><b id="bage">—</b></div></div></article>
<article class="card"><div class="row"><div><div class="label">Zatížení</div><div class="big" id="load">—</div></div><div><div class="label">Výkon</div><div class="big" id="watts">—</div></div></div><div class="bar"><i id="lfill"></i></div><div class="sub" id="maxpower">—</div></article>
<article class="card full"><h2>Síť a UPS</h2><div class="stats section"><div class="stat"><span class="label">Síť</span><b id="linev">—</b></div><div class="stat"><span class="label">Povolené napětí</span><b id="range">—</b></div><div class="stat"><span class="label">Citlivost</span><b id="sense">—</b></div><div class="stat"><span class="label">Přechodů na baterii</span><b id="xfers">—</b></div><div class="stat"><span class="label">Právě na baterii</span><b id="tonbatt">—</b></div><div class="stat"><span class="label">Celkem na baterii</span><b id="cumonbatt">—</b></div><div class="stat"><span class="label">Poslední přechod</span><b id="last">—</b></div><div class="stat"><span class="label">Self-test</span><b id="selftest">—</b></div><div class="stat"><span class="label">Alarm</span><b id="alarmdel">—</b></div><div class="stat"><span class="label">Vypnout při baterii</span><b id="minb">—</b></div><div class="stat"><span class="label">Vypnout při výdrži</span><b id="mint">—</b></div><div class="stat"><span class="label">Timeout</span><b id="maxt">—</b></div></div></article>
<article class="card full"><div class="row"><div><h2>Historie UPS</h2><div class="sub small">Každá veličina má vlastní graf a vlastní jednotku.</div></div><div class="tabs" id="historyTabs"><button class="btn" data-hours="6">6 h</button><button class="btn active" data-hours="24">24 h</button><button class="btn" data-hours="168">7 dní</button><button class="btn" data-hours="720">30 dní</button></div></div>
<div class="history-grid">
<div class="history-card"><div class="history-head"><div><div class="history-title">Baterie</div><div class="history-unit">nabití v %</div></div><div class="history-stats" id="stats-battery">—</div></div><div class="chart-wrap"><canvas class="chart" id="chart-battery"></canvas></div><div class="chart-detail" id="detail-battery">Přejeď myší nebo klepni do grafu pro přesnou hodnotu.</div></div>
<div class="history-card"><div class="history-head"><div><div class="history-title">Zatížení UPS</div><div class="history-unit">zatížení v %</div></div><div class="history-stats" id="stats-load">—</div></div><div class="chart-wrap"><canvas class="chart" id="chart-load"></canvas></div><div class="chart-detail" id="detail-load">Přejeď myší nebo klepni do grafu pro přesnou hodnotu.</div></div>
<div class="history-card"><div class="history-head"><div><div class="history-title">Napětí sítě</div><div class="history-unit">volty (V)</div></div><div class="history-stats" id="stats-linev">—</div></div><div class="chart-wrap"><canvas class="chart" id="chart-linev"></canvas></div><div class="chart-detail" id="detail-linev">Přejeď myší nebo klepni do grafu pro přesnou hodnotu.</div></div>
<div class="history-card"><div class="history-head"><div><div class="history-title">Odhadovaná výdrž</div><div class="history-unit">minuty</div></div><div class="history-stats" id="stats-runtime">—</div></div><div class="chart-wrap"><canvas class="chart" id="chart-runtime"></canvas></div><div class="chart-detail" id="detail-runtime">Přejeď myší nebo klepni do grafu pro přesnou hodnotu.</div></div>
<div class="history-card"><div class="history-head"><div><div class="history-title">Příkon z UPS</div><div class="history-unit">přibližný výkon ve W</div></div><div class="history-stats" id="stats-watts">—</div></div><div class="chart-wrap"><canvas class="chart" id="chart-watts"></canvas></div><div class="chart-detail" id="detail-watts">Přejeď myší nebo klepni do grafu pro přesnou hodnotu.</div></div>
<div class="history-card"><div class="history-head"><div><div class="history-title">Napětí baterie</div><div class="history-unit">volty (V)</div></div><div class="history-stats" id="stats-battv">—</div></div><div class="chart-wrap"><canvas class="chart" id="chart-battv"></canvas></div><div class="chart-detail" id="detail-battv">Přejeď myší nebo klepni do grafu pro přesnou hodnotu.</div></div>
</div><div class="sub small history-note">Historie se ukládá každých {{poll}} s. Nad každým grafem je minimum, průměr, maximum a poslední hodnota za zvolené období.</div></article>
<article class="card"><h2>Výpadky</h2><div id="outages" class="section">Načítám…</div></article>
<article class="card"><h2>Události apcupsd</h2><div id="events" class="events section">Načítám…</div></article>
<article class="card full"><h2>Všechny údaje z UPS</h2><table class="table section" id="raw"><tbody></tbody></table></article></section><div class="error" id="error"></div></main>
<script>
const $=x=>document.getElementById(x),f=(v,u='')=>v==null?'—':`${v}${u}`;let historyHours=24;
function secs(v){if(v==null)return'—';v=Math.round(v);let h=Math.floor(v/3600),m=Math.floor((v%3600)/60),s=v%60;return(h?h+' h ':'')+(m?m+' min ':'')+s+' s'}
function esc(s){let d=document.createElement('div');d.textContent=s??'';return d.innerHTML}
async function update(){try{let r=await fetch('/api/status',{cache:'no-store'}),j=await r.json();if(!r.ok)throw Error(j.error||'Chyba');$('model').textContent=j.model;$('name').textContent=j.name+(j.serial?' · S/N '+j.serial:'')+(j.firmware?' · FW '+j.firmware:'');let s=j.status||'UNKNOWN';$('pill').textContent=j.low_battery?'NÍZKÁ BATERIE':j.on_battery?'NA BATERII':j.online?'ONLINE':s;$('pill').className='pill '+(j.low_battery?'bad':j.on_battery?'batt':j.online?'online':'bad');$('battery').textContent=f(j.battery,' %');$('runtime').textContent=f(j.runtime,' min');$('bfill').style.width=Math.max(0,Math.min(100,j.battery||0))+'%';$('battv').textContent=f(j.battv,' V');$('nombattv').textContent=f(j.nombattv,' V');$('bdate').textContent=j.battery_date||'—';$('health').textContent=j.battery_condition||'NEZNÁMÁ';$('health').className='health '+(j.battery_condition_code||'unknown');$('healthreason').textContent=j.battery_condition_reason||'—';$('bage').textContent=j.battery_age_years==null?'—':j.battery_age_years.toFixed(1)+' roku';$('load').textContent=f(j.load,' %');$('watts').textContent=f(j.watts,' W');$('lfill').style.width=Math.max(0,Math.min(100,j.load||0))+'%';$('maxpower').textContent='Maximum UPS: '+f(j.power,' W');$('linev').textContent=f(j.linev,' V');$('range').textContent=(j.lowtrans==null||j.hitrans==null)?'—':j.lowtrans+'–'+j.hitrans+' V';$('sense').textContent=j.sense||'—';$('xfers').textContent=f(j.num_transfers);$('tonbatt').textContent=secs(j.time_on_battery);$('cumonbatt').textContent=secs(j.cumulative_on_battery);$('last').textContent=j.last_transfer||'—';$('selftest').textContent=j.selftest||'—';$('alarmdel').textContent=j.alarmdel||'—';$('minb').textContent=f(j.min_batt,' %');$('mint').textContent=f(j.min_time,' min');$('maxt').textContent=f(j.max_time,' s');let tb=$('raw').querySelector('tbody');tb.innerHTML='';Object.entries(j.raw||{}).forEach(([k,v])=>{let tr=document.createElement('tr'),a=document.createElement('td'),b=document.createElement('td');a.textContent=k;b.textContent=v;tr.append(a,b);tb.append(tr)});$('error').style.display='none'}catch(e){$('error').textContent='UPS: '+e.message;$('error').style.display='block'}}
async function loadEvents(){try{let j=await (await fetch('/api/events',{cache:'no-store'})).json();$('events').innerHTML=(j.events||[]).map(x=>'<div class="event">'+esc(x)+'</div>').join('')||'<span class="sub">Žádné události.</span>'}catch(e){}}
async function loadOutages(){try{let j=await (await fetch('/api/outages',{cache:'no-store'})).json();$('outages').innerHTML=(j.outages||[]).map(o=>`<div class="outage"><span>${new Date(o.started*1000).toLocaleString()}</span><span>${o.ended?secs(o.duration):'probíhá'}</span><span>${f(o.start_battery,'%')} → ${f(o.end_battery,'%')}</span><span class="sub">${esc(o.reason||'')}</span></div>`).join('')||'<span class="sub">Zatím žádný zaznamenaný výpadek.</span>'}catch(e){}}
let historyRows=[];
const historySpecs=[
  {key:'battery',canvas:'chart-battery',stats:'stats-battery',detail:'detail-battery',unit:'%',fixedMin:0,fixedMax:100,decimals:0},
  {key:'load',canvas:'chart-load',stats:'stats-load',detail:'detail-load',unit:'%',fixedMin:0,fixedMax:100,decimals:0},
  {key:'linev',canvas:'chart-linev',stats:'stats-linev',detail:'detail-linev',unit:' V',decimals:0,pad:4},
  {key:'runtime',canvas:'chart-runtime',stats:'stats-runtime',detail:'detail-runtime',unit:' min',decimals:1,pad:2},
  {key:'watts',canvas:'chart-watts',stats:'stats-watts',detail:'detail-watts',unit:' W',decimals:0,pad:8},
  {key:'battv',canvas:'chart-battv',stats:'stats-battv',detail:'detail-battv',unit:' V',decimals:1,pad:1}
];
function metricValues(rows,key){return rows.map(r=>r[key]).filter(v=>v!=null&&Number.isFinite(Number(v))).map(Number)}
function fmtMetric(v,spec){if(v==null||!Number.isFinite(Number(v)))return'—';return Number(v).toFixed(spec.decimals)+spec.unit}
function fmtAxis(v,spec){if(!Number.isFinite(v))return'—';return Number(v).toFixed(spec.decimals)+(spec.unit.trim()==='%'?'%':'')}
function fmtMoment(ts){let d=new Date(ts*1000);return historyHours<=24?d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):d.toLocaleString([],{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}
function updateMetricStats(rows,spec){let vals=metricValues(rows,spec.key),el=$(spec.stats);if(!vals.length){el.textContent='Bez dat';return}let min=Math.min(...vals),max=Math.max(...vals),avg=vals.reduce((a,b)=>a+b,0)/vals.length,last=vals.at(-1);el.innerHTML=`<span>Min <b>${fmtMetric(min,spec)}</b></span><span>Průměr <b>${fmtMetric(avg,spec)}</b></span><span>Max <b>${fmtMetric(max,spec)}</b></span><span>Poslední <b>${fmtMetric(last,spec)}</b></span>`}
function drawMetricChart(rows,spec){
  let canvas=$(spec.canvas),dpr=devicePixelRatio||1,w=Math.max(280,canvas.clientWidth),h=canvas.clientHeight||220;
  canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);let ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
  updateMetricStats(rows,spec);
  let points=rows.filter(r=>r[spec.key]!=null&&Number.isFinite(Number(r[spec.key]))).map(r=>({ts:r.ts,v:Number(r[spec.key])}));
  if(!points.length){ctx.fillStyle='#93a4bd';ctx.font='13px system-ui';ctx.fillText('Zatím nejsou data pro tuto veličinu.',16,28);return}
  let padL=54,padR=16,padT=18,padB=34,t0=points[0].ts,t1=points.at(-1).ts;if(t1===t0)t1=t0+1;
  let vals=points.map(p=>p.v),vMin=spec.fixedMin!=null?spec.fixedMin:Math.min(...vals),vMax=spec.fixedMax!=null?spec.fixedMax:Math.max(...vals);
  if(spec.fixedMin==null){let span=Math.max(vMax-vMin,Math.abs(vMax)*.02,spec.pad||1);vMin-=Math.max(spec.pad||0,span*.15);vMax+=Math.max(spec.pad||0,span*.15)}
  if(vMax===vMin){vMax=vMin+1}
  let px=t=>padL+(t-t0)/(t1-t0)*(w-padL-padR),py=v=>padT+(vMax-v)/(vMax-vMin)*(h-padT-padB);
  ctx.font='11px system-ui';ctx.textBaseline='middle';
  for(let i=0;i<=4;i++){let frac=i/4,y=padT+frac*(h-padT-padB),v=vMax-frac*(vMax-vMin);ctx.strokeStyle='#ffffff18';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(w-padR,y);ctx.stroke();ctx.fillStyle='#93a4bd';ctx.textAlign='right';ctx.fillText(fmtAxis(v,spec),padL-7,y)}
  let times=[t0,(t0+t1)/2,t1];ctx.textBaseline='top';
  times.forEach((t,i)=>{let x=px(t);ctx.fillStyle='#93a4bd';ctx.textAlign=i===0?'left':i===2?'right':'center';ctx.fillText(fmtMoment(t),x,h-padB+9)});
  ctx.strokeStyle='#5aa7ff';ctx.lineWidth=2.5;ctx.lineJoin='round';ctx.lineCap='round';ctx.beginPath();points.forEach((p,i)=>{let x=px(p.ts),y=py(p.v);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)});ctx.stroke();
  canvas._historyMeta={points,px,py,spec,w,h,padL,padR,padT,padB};
}
function redrawHistory(){historySpecs.forEach(spec=>drawMetricChart(historyRows,spec))}
function showNearest(canvas,clientX){
  let meta=canvas._historyMeta;if(!meta||!meta.points.length)return;
  let rect=canvas.getBoundingClientRect(),x=Math.max(meta.padL,Math.min(meta.w-meta.padR,clientX-rect.left));
  let targetTs=meta.points[0].ts+(x-meta.padL)/(meta.w-meta.padL-meta.padR)*(meta.points.at(-1).ts-meta.points[0].ts||1);
  let nearest=meta.points.reduce((best,p)=>Math.abs(p.ts-targetTs)<Math.abs(best.ts-targetTs)?p:best,meta.points[0]);
  $(meta.spec.detail).textContent=`${new Date(nearest.ts*1000).toLocaleString()} · ${fmtMetric(nearest.v,meta.spec)}`;
  drawMetricChart(historyRows,meta.spec);
  let m=canvas._historyMeta,ctx=canvas.getContext('2d'),xPoint=m.px(nearest.ts),yPoint=m.py(nearest.v),dpr=devicePixelRatio||1;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.strokeStyle='#ffffff66';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(xPoint,m.padT);ctx.lineTo(xPoint,m.h-m.padB);ctx.stroke();ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(xPoint,yPoint,4,0,Math.PI*2);ctx.fill();
}
async function loadHistory(){
  try{
    let j=await (await fetch('/api/history?hours='+historyHours,{cache:'no-store'})).json();
    historyRows=j.samples||[];
    redrawHistory();
  }catch(e){}
}
document.querySelectorAll('#historyTabs [data-hours]').forEach(btn=>btn.addEventListener('click',()=>{historyHours=Number(btn.dataset.hours);document.querySelectorAll('#historyTabs [data-hours]').forEach(b=>b.classList.toggle('active',b===btn));loadHistory()}));
historySpecs.forEach(spec=>{let canvas=$(spec.canvas);canvas.addEventListener('pointermove',e=>showNearest(canvas,e.clientX));canvas.addEventListener('pointerdown',e=>showNearest(canvas,e.clientX))});
update();loadHistory();loadEvents();loadOutages();setInterval(update,5000);setInterval(()=>{loadHistory();loadEvents();loadOutages()},30000);let resizeTimer;addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(redrawHistory,120)});
</script></body></html>'''

LOGIN = r'''<!doctype html><html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>UPS – přihlášení</title><style>body{font-family:system-ui;background:#07101f;color:#fff;display:grid;place-items:center;min-height:100vh;margin:0}.box{width:min(420px,92vw);background:#121f35;padding:26px;border-radius:18px}input,button{width:100%;padding:12px;margin:7px 0;border-radius:10px;border:1px solid #ffffff22;background:#0d1829;color:#fff}button{background:#285ca8;font-weight:800}.err{color:#ff8e96}</style></head><body><form class="box" method="post"><h1>Nastavení UPS</h1><p>Přihlášení správce.</p>{% with m=get_flashed_messages() %}{% if m %}<p class="err">{{m[-1]}}</p>{% endif %}{% endwith %}<input type="hidden" name="csrf" value="{{csrf}}"><input name="username" autocomplete="username" placeholder="Uživatel" required><input name="password" type="password" autocomplete="current-password" placeholder="Heslo" required><button>Přihlásit</button><p><a style="color:#9bc7ff" href="/">← Zpět na dashboard</a></p></form></body></html>'''

ADMIN = r'''<!doctype html><html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>UPS – nastavení</title><style>body{font-family:system-ui;background:#07101f;color:#fff;margin:0}.wrap{max-width:850px;margin:auto;padding:25px 16px}.card{background:#121f35;padding:20px;border-radius:18px;margin-bottom:14px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}label{display:block;color:#a8b6ca}input,select,button{width:100%;padding:11px;margin-top:5px;border-radius:9px;border:1px solid #ffffff22;background:#0d1829;color:#fff}button{background:#285ca8;font-weight:800}.ok{background:#164c34;padding:10px;border-radius:9px}.err{background:#5a2229;padding:10px;border-radius:9px}.top{display:flex;justify-content:space-between;gap:10px;align-items:center}.link{color:#9bc7ff}.muted{color:#93a4bd}.fw{background:#0d1829;padding:14px;border-radius:12px}.fw h3{margin:0 0 8px}.current{font-size:14px;color:#93a4bd;margin:0 0 8px}.confirm{display:flex;align-items:flex-start;gap:8px;margin-top:10px;font-size:14px}.confirm input{width:auto;margin:2px 0 0}.warn{background:#4a3516;padding:11px;border-radius:10px;color:#ffd98a}@media(max-width:650px){.grid{grid-template-columns:1fr}}</style></head><body><main class="wrap"><div class="top"><div><h1>Nastavení UPS</h1><a class="link" href="/">← Dashboard</a></div><form method="post" action="/admin/logout"><input type="hidden" name="csrf" value="{{csrf}}"><button>Odhlásit</button></form></div>{% with msgs=get_flashed_messages(with_categories=true) %}{% for cat,msg in msgs %}<p class="{{cat}}">{{msg}}</p>{% endfor %}{% endwith %}
<form class="card" method="post"><input type="hidden" name="csrf" value="{{csrf}}"><h2>Automatické vypnutí serveru</h2><div class="grid"><label>Zpoždění reakce na výpadek – ONBATTERYDELAY (s)<input type="number" min="0" max="3600" name="ONBATTERYDELAY" value="{{cfg.get('ONBATTERYDELAY','6')}}" required></label><label>Baterie – vypnout při %<input type="number" min="0" max="100" name="BATTERYLEVEL" value="{{cfg.get('BATTERYLEVEL','10')}}" required></label><label>Zbývající výdrž – minuty<input type="number" min="0" max="120" name="MINUTES" value="{{cfg.get('MINUTES','5')}}" required></label><label>Max. čas na baterii – sekundy<input type="number" min="0" max="86400" name="TIMEOUT" value="{{cfg.get('TIMEOUT','0')}}" required></label><label>Zpoždění vypnutí výstupu UPS – KILLDELAY (s)<input type="number" min="0" max="86400" name="KILLDELAY" value="{{cfg.get('KILLDELAY','0')}}" required></label></div><p class="muted">BATTERYLEVEL, MINUTES a TIMEOUT fungují společně; první splněná podmínka zahájí vypnutí. TIMEOUT 0 časový limit vypíná. ONBATTERYDELAY odfiltruje velmi krátké výpadky pro událost onbattery.</p><button>Uložit a restartovat apcupsd</button></form>
<form class="card" method="post"><input type="hidden" name="csrf" value="{{csrf}}"><h2>Upozornění apcupsd</h2><div class="grid"><label>ANNOY – opakování upozornění (s)<input type="number" min="0" max="86400" name="ANNOY" value="{{cfg.get('ANNOY','300')}}" required></label><label>ANNOYDELAY – první upozornění (s)<input type="number" min="0" max="86400" name="ANNOYDELAY" value="{{cfg.get('ANNOYDELAY','60')}}" required></label><label>NOLOGON<select name="NOLOGON">{% for v in ['disable','timeout','percent','minutes','always'] %}<option value="{{v}}" {% if cfg.get('NOLOGON','disable')==v %}selected{% endif %}>{{v}}</option>{% endfor %}</select></label></div><button>Uložit a restartovat apcupsd</button></form>
<form class="card" method="post"><input type="hidden" name="csrf" value="{{csrf}}"><h2>Pokročilé nastavení démona</h2><div class="grid"><label>Název UPS – UPSNAME<input name="UPSNAME" minlength="1" maxlength="8" pattern="[A-Za-z0-9_.-]{1,8}" value="{{cfg.get('UPSNAME','RS1000')}}" required></label><label>Interval dotazování UPS – POLLTIME (s)<input type="number" min="1" max="3600" name="POLLTIME" value="{{cfg.get('POLLTIME','60')}}" required></label><label>Velikost logu událostí – EVENTSFILEMAX (KB)<input type="number" min="1" max="10240" name="EVENTSFILEMAX" value="{{cfg.get('EVENTSFILEMAX','10')}}" required></label><label>Zápis STATUS souboru – STATTIME (s)<input type="number" min="0" max="86400" name="STATTIME" value="{{cfg.get('STATTIME','0')}}" required></label><label>Zápis DATA záznamů – DATATIME (s)<input type="number" min="0" max="86400" name="DATATIME" value="{{cfg.get('DATATIME','0')}}" required></label><label>Podrobné statistiky do syslogu – LOGSTATS<select name="LOGSTATS"><option value="off" {% if cfg.get('LOGSTATS','off')=='off' %}selected{% endif %}>Vypnuto</option><option value="on" {% if cfg.get('LOGSTATS','off')=='on' %}selected{% endif %}>Zapnuto</option></select></label></div><p class="muted">Nižší POLLTIME zrychlí reakci, ale zvyšuje počet dotazů na UPS. STATTIME 0 a DATATIME 0 příslušné pravidelné zápisy vypínají. LOGSTATS může vytvářet hodně záznamů.</p><button>Uložit a restartovat apcupsd</button></form>
<div class="card"><h2>Síťové propojení dashboardu</h2><p>NETSERVER, NISIP a NISPORT jsou kvůli Docker propojení záměrně uzamčené na <code>on / 127.0.0.1 / 3551</code>. Jejich změna by odpojila dashboard od apcupsd.</p></div>
<div class="card"><h2>Nastavení přímo v UPS</h2><p class="warn">Tyto hodnoty se zapisují přímo do UPS přes <code>apctest</code>. Při změně musí být UPS ONLINE. <code>apcupsd</code> se na několik sekund zastaví a po dokončení automaticky znovu spustí. Nepodporovanou hodnotu UPS odmítne.</p><div class="grid">
<form class="fw" method="post"><input type="hidden" name="csrf" value="{{csrf}}"><input type="hidden" name="firmware_action" value="sensitivity"><h3>Citlivost</h3><p class="current">Aktuálně: <b>{{ups.get('sense') or '—'}}</b></p><label>Nová citlivost<select name="firmware_value" required><option value="low" {% if (ups.get('sense') or '')|lower == 'low' %}selected{% endif %}>Low</option><option value="medium" {% if (ups.get('sense') or '')|lower == 'medium' %}selected{% endif %}>Medium</option><option value="high" {% if (ups.get('sense') or '')|lower == 'high' %}selected{% endif %}>High</option></select></label><label class="confirm"><input type="checkbox" name="confirm_firmware" value="yes" required>Potvrzuji zápis přímo do UPS.</label><button>Změnit citlivost</button></form>
<form class="fw" method="post"><input type="hidden" name="csrf" value="{{csrf}}"><input type="hidden" name="firmware_action" value="low_transfer"><h3>Dolní přepínací napětí</h3><p class="current">Aktuálně: <b>{{ups.get('lowtrans') if ups.get('lowtrans') is not none else '—'}} V</b></p><label>Nová hodnota (V)<input type="number" min="80" max="300" name="firmware_value" value="{{ups.get('lowtrans')|int if ups.get('lowtrans') is not none else ''}}" required></label><label class="confirm"><input type="checkbox" name="confirm_firmware" value="yes" required>Potvrzuji zápis přímo do UPS.</label><button>Změnit dolní mez</button></form>
<form class="fw" method="post"><input type="hidden" name="csrf" value="{{csrf}}"><input type="hidden" name="firmware_action" value="high_transfer"><h3>Horní přepínací napětí</h3><p class="current">Aktuálně: <b>{{ups.get('hitrans') if ups.get('hitrans') is not none else '—'}} V</b></p><label>Nová hodnota (V)<input type="number" min="80" max="300" name="firmware_value" value="{{ups.get('hitrans')|int if ups.get('hitrans') is not none else ''}}" required></label><label class="confirm"><input type="checkbox" name="confirm_firmware" value="yes" required>Potvrzuji zápis přímo do UPS.</label><button>Změnit horní mez</button></form>
<form class="fw" method="post"><input type="hidden" name="csrf" value="{{csrf}}"><input type="hidden" name="firmware_action" value="alarm"><h3>Zvukový alarm</h3><p class="current">UPS hlásí: <b>{{ups.get('alarmdel') or '—'}}</b></p><label>Chování alarmu<select name="firmware_value" required><option value="" selected disabled>Vyber…</option><option value="enabled">Zapnout</option><option value="disabled">Vypnout</option></select></label><label class="confirm"><input type="checkbox" name="confirm_firmware" value="yes" required>Potvrzuji zápis přímo do UPS.</label><button>Změnit alarm</button></form>
<form class="fw" method="post"><input type="hidden" name="csrf" value="{{csrf}}"><input type="hidden" name="firmware_action" value="battery_date"><h3>Datum baterie</h3><p class="current">Aktuálně: <b>{{ups.get('battery_date') or '—'}}</b></p><label>Datum instalace/výměny baterie<input type="date" name="firmware_value" value="{{battery_date_input}}" min="1980-01-01" max="{{today}}" required></label><label class="confirm"><input type="checkbox" name="confirm_firmware" value="yes" required>Potvrzuji zápis přímo do UPS.</label><button>Změnit datum baterie</button></form>
</div><p class="muted">Povolené hodnoty dolní a horní meze určuje firmware konkrétní UPS. Když BR1000I požadovanou hodnotu nepodporuje, změna se neoznačí jako úspěšná.</p></div></main></body></html>'''


@app.get("/")
def index():
    return render_template_string(PAGE, poll=POLL_SECONDS)


@app.get("/api/status")
def api_status():
    try:
        return jsonify(status_data())
    except Exception as e:
        return jsonify(error=str(e)), 503


@app.get("/api/events")
def api_events():
    return jsonify(events=read_events())


@app.get("/api/history")
def api_history():
    hours = min(max(request.args.get("hours", 24, type=int), 1), RETENTION_DAYS * 24)
    cutoff = int(time.time()) - hours * 3600
    with contextlib.closing(db_connect()) as db:
        rows = db.execute(
            "SELECT ts,status,linev,battery,runtime,load,watts,battv FROM samples WHERE ts>=? ORDER BY ts",
            (cutoff,),
        ).fetchall()
    # Keep long periods responsive on the Raspberry Pi and in mobile browsers.
    # The first/last sample are retained; intermediate samples are thinned only
    # for graph rendering, never deleted from the history database.
    if len(rows) > 1800:
        step = max(1, len(rows) // 1798)
        selected = list(rows[::step])
        if selected[-1]["ts"] != rows[-1]["ts"]:
            selected.append(rows[-1])
        rows = selected
    return jsonify(samples=[dict(row) for row in rows])


@app.get("/api/outages")
def api_outages():
    with contextlib.closing(db_connect()) as db:
        rows = db.execute(
            "SELECT started_ts,ended_ts,start_battery,end_battery,reason FROM outages ORDER BY started_ts DESC LIMIT 100"
        ).fetchall()
    now = int(time.time())
    return jsonify(outages=[
        {
            "started": row["started_ts"],
            "ended": row["ended_ts"],
            "duration": (row["ended_ts"] or now) - row["started_ts"],
            "start_battery": row["start_battery"],
            "end_battery": row["end_battery"],
            "reason": row["reason"],
        }
        for row in rows
    ])


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if not ADMIN_PASSWORD_HASH:
        return "Administrátorské heslo není nastavené. Spusť znovu install.sh.", 503
    if request.method == "POST":
        try:
            require_csrf()
        except ValueError as e:
            flash(str(e))
            return redirect(url_for("admin_login"))
        if request.form.get("username", "") == ADMIN_USER and check_password_hash(ADMIN_PASSWORD_HASH, request.form.get("password", "")):
            session.clear()
            session.permanent = True
            session["admin"] = True
            session["csrf"] = secrets.token_urlsafe(32)
            return redirect(url_for("admin"))
        flash("Nesprávné jméno nebo heslo.")
    return render_template_string(LOGIN, csrf=csrf_token())


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    if request.method == "POST":
        try:
            require_csrf()
            firmware_action = request.form.get("firmware_action", "").strip()
            if firmware_action:
                if request.form.get("confirm_firmware") != "yes":
                    raise ValueError("Změnu přímo v UPS je nutné potvrdit.")
                firmware_value = request.form.get("firmware_value", "").strip()
                result = bridge_request(
                    "firmware_apply",
                    setting=firmware_action,
                    value=firmware_value,
                )
                flash("Nastavení přímo v UPS bylo změněno a ověřeno.", "ok")
                return redirect(url_for("admin"))

            values = {
                "ONBATTERYDELAY": request.form.get("ONBATTERYDELAY", ""),
                "BATTERYLEVEL": request.form.get("BATTERYLEVEL", ""),
                "MINUTES": request.form.get("MINUTES", ""),
                "TIMEOUT": request.form.get("TIMEOUT", ""),
                "KILLDELAY": request.form.get("KILLDELAY", ""),
                "ANNOY": request.form.get("ANNOY", ""),
                "ANNOYDELAY": request.form.get("ANNOYDELAY", ""),
                "NOLOGON": request.form.get("NOLOGON", ""),
                "UPSNAME": request.form.get("UPSNAME", ""),
                "POLLTIME": request.form.get("POLLTIME", ""),
                "EVENTSFILEMAX": request.form.get("EVENTSFILEMAX", ""),
                "STATTIME": request.form.get("STATTIME", ""),
                "DATATIME": request.form.get("DATATIME", ""),
                "LOGSTATS": request.form.get("LOGSTATS", ""),
            }
            bridge_apply({key: value for key, value in values.items() if value != ""})
            flash("Nastavení bylo uloženo a apcupsd restartován.", "ok")
        except Exception as e:
            flash(str(e), "err")
        return redirect(url_for("admin"))
    try:
        ups = status_data()
    except Exception:
        ups = {}
    battery_date_input = ""
    raw_date = str(ups.get("battery_date") or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
        battery_date_input = raw_date
    else:
        for fmt in ("%m/%d/%Y", "%m/%d/%y"):
            try:
                battery_date_input = dt.datetime.strptime(raw_date, fmt).date().isoformat()
                break
            except ValueError:
                pass
    return render_template_string(
        ADMIN,
        cfg=read_config_values(),
        ups=ups,
        battery_date_input=battery_date_input,
        today=dt.date.today().isoformat(),
        csrf=csrf_token(),
    )


@app.post("/admin/logout")
@admin_required
def admin_logout():
    try:
        require_csrf()
    except ValueError:
        pass
    session.clear()
    return redirect(url_for("index"))


@app.get("/healthz")
def health():
    try:
        status_data()
        with contextlib.closing(db_connect()) as db:
            db.execute("SELECT 1").fetchone()
        return jsonify(ok=True, version=APP_VERSION)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 503


if __name__ == "__main__":
    app.run("127.0.0.1", 8011)

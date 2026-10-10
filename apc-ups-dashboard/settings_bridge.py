#!/usr/bin/env python3
from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import socketserver
import subprocess
import time
from pathlib import Path

CONFIG = Path(os.environ.get("UPS_APC_CONFIG_HOST", "/etc/apcupsd/apcupsd.conf"))
EVENTS_FILE = Path(os.environ.get("UPS_EVENTS_FILE_HOST", "/var/log/apcupsd.events"))
SOCKET_PATH = Path(os.environ.get("UPS_SETTINGS_SOCKET", "/run/apc-ups-dashboard/settings.sock"))
BRIDGE_TOKEN = os.environ.get("UPS_BRIDGE_TOKEN", "").strip()
SOCKET_GID = int(os.environ.get("UPS_BRIDGE_GID", "10001"))

ALLOWED = {
    "BATTERYLEVEL": ("int", 0, 100),
    "MINUTES": ("int", 0, 120),
    "TIMEOUT": ("int", 0, 86400),
    "ANNOY": ("int", 0, 86400),
    "ANNOYDELAY": ("int", 0, 86400),
    "KILLDELAY": ("int", 0, 86400),
    "NOLOGON": ("choice", {"disable", "timeout", "percent", "minutes", "always"}),
}


def validate(key: str, value: str) -> str:
    if key not in ALLOWED:
        raise ValueError(f"Nepovolené nastavení: {key}")
    spec = ALLOWED[key]
    value = str(value).strip()
    if spec[0] == "int":
        if not re.fullmatch(r"\d+", value):
            raise ValueError(f"{key}: očekává se celé nezáporné číslo.")
        number = int(value)
        if not spec[1] <= number <= spec[2]:
            raise ValueError(f"{key}: povolený rozsah je {spec[1]}–{spec[2]}.")
        return str(number)
    if value not in spec[1]:
        raise ValueError(f"{key}: nepovolená hodnota.")
    return value


def read_config() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in CONFIG.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        if len(parts) == 2 and parts[0] in ALLOWED:
            result[parts[0]] = parts[1].strip()
    return result


def write_config(changes: dict[str, str]) -> None:
    validated = {key: validate(key, value) for key, value in changes.items()}
    original = CONFIG.read_text(encoding="utf-8")
    lines = original.splitlines()
    found: set[str] = set()

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        if len(parts) == 2 and parts[0] in validated:
            key = parts[0]
            lines[index] = f"{key} {validated[key]}"
            found.add(key)

    for key, value in validated.items():
        if key not in found:
            lines.append(f"{key} {value}")

    new_text = "\n".join(lines) + "\n"
    if new_text == original:
        return

    backup = CONFIG.with_name(f"{CONFIG.name}.dashboard-{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(CONFIG, backup)
    tmp = CONFIG.with_name(f".{CONFIG.name}.dashboard.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.chmod(tmp, CONFIG.stat().st_mode & 0o777)
    os.replace(tmp, CONFIG)

    try:
        subprocess.run(["/bin/systemctl", "restart", "apcupsd.service"], check=True, timeout=20)
        subprocess.run(["/bin/systemctl", "is-active", "--quiet", "apcupsd.service"], check=True, timeout=10)
    except Exception:
        shutil.copy2(backup, CONFIG)
        subprocess.run(["/bin/systemctl", "restart", "apcupsd.service"], check=False, timeout=20)
        raise RuntimeError("Nové nastavení apcupsd neprošlo. Původní konfigurace byla obnovena.")


def read_events(limit: int) -> list[str]:
    limit = max(1, min(limit, 500))
    try:
        lines = EVENTS_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip()][-limit:][::-1]


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            raw = self.rfile.readline(262145)
            if not raw or len(raw) > 262144:
                raise ValueError("Neplatný požadavek.")
            request = json.loads(raw.decode("utf-8"))
            token = str(request.get("token", ""))
            if not BRIDGE_TOKEN or not hmac.compare_digest(token, BRIDGE_TOKEN):
                raise PermissionError("Neplatné oprávnění.")

            action = request.get("action")
            if action == "get_config":
                response = {"ok": True, "config": read_config()}
            elif action == "events":
                response = {"ok": True, "events": read_events(int(request.get("limit", 120)))}
            elif action == "apply":
                changes = request.get("changes")
                if not isinstance(changes, dict) or not changes:
                    raise ValueError("Chybí nastavení.")
                write_config({str(k): str(v) for k, v in changes.items()})
                response = {"ok": True, "config": read_config()}
            else:
                raise ValueError("Neznámá operace.")
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}

        self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main() -> None:
    if not BRIDGE_TOKEN:
        raise SystemExit("Chybí UPS_BRIDGE_TOKEN.")

    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chown(SOCKET_PATH.parent, 0, SOCKET_GID)
    os.chmod(SOCKET_PATH.parent, 0o770)
    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass

    with Server(str(SOCKET_PATH), Handler) as server:
        os.chown(SOCKET_PATH, 0, SOCKET_GID)
        os.chmod(SOCKET_PATH, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()

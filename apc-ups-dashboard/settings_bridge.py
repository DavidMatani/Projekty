#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import hmac
import json
import os
import re
import shutil
import socketserver
import subprocess
import threading
import time
from pathlib import Path

CONFIG = Path(os.environ.get("UPS_APC_CONFIG_HOST", "/etc/apcupsd/apcupsd.conf"))
EVENTS_FILE = Path(os.environ.get("UPS_EVENTS_FILE_HOST", "/var/log/apcupsd.events"))
SOCKET_PATH = Path(os.environ.get("UPS_SETTINGS_SOCKET", "/run/apc-ups-dashboard/settings.sock"))
BRIDGE_TOKEN = os.environ.get("UPS_BRIDGE_TOKEN", "").strip()
SOCKET_GID = int(os.environ.get("UPS_BRIDGE_GID", "10001"))
APCTEST = shutil.which("apctest") or "/usr/sbin/apctest"
APCACCESS = shutil.which("apcaccess") or "/usr/sbin/apcaccess"
_WRITE_LOCK = threading.Lock()

ALLOWED = {
    # Power-failure behaviour
    "ONBATTERYDELAY": ("int", 0, 3600),
    "BATTERYLEVEL": ("int", 0, 100),
    "MINUTES": ("int", 0, 120),
    "TIMEOUT": ("int", 0, 86400),
    "ANNOY": ("int", 0, 86400),
    "ANNOYDELAY": ("int", 0, 86400),
    "KILLDELAY": ("int", 0, 86400),
    "NOLOGON": ("choice", {"disable", "timeout", "percent", "minutes", "always"}),

    # Safe daemon/monitoring options. Network settings are intentionally
    # excluded because the Docker dashboard depends on 127.0.0.1:3551.
    "POLLTIME": ("int", 1, 3600),
    "EVENTSFILEMAX": ("int", 1, 10240),
    "STATTIME": ("int", 0, 86400),
    "DATATIME": ("int", 0, 86400),
    "LOGSTATS": ("choice", {"on", "off"}),
    "UPSNAME": ("name",),
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
    if spec[0] == "choice":
        if value not in spec[1]:
            raise ValueError(f"{key}: nepovolená hodnota.")
        return value
    if spec[0] == "name":
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,8}", value):
            raise ValueError(f"{key}: použij 1–8 znaků A-Z, a-z, 0-9, tečku, pomlčku nebo podtržítko.")
        return value
    raise ValueError(f"{key}: neznámý typ nastavení.")


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


def _parse_status() -> dict[str, str]:
    process = subprocess.run(
        [APCACCESS, "status"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    result: dict[str, str] = {}
    for line in process.stdout.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def _require_usb_online() -> dict[str, str]:
    config: dict[str, str] = {}
    for line in CONFIG.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        if len(parts) == 2:
            config[parts[0].upper()] = parts[1].strip()

    if config.get("UPSCABLE", "").lower() != "usb" or config.get("UPSTYPE", "").lower() != "usb":
        raise RuntimeError("Změny firmware přes web jsou povolené pouze pro UPS připojenou přes USB.")

    status = _parse_status()
    state = status.get("STATUS", "")
    if "ONLINE" not in state or "ONBATT" in state:
        raise RuntimeError("UPS musí být ONLINE na síti. Nastavení firmware se při běhu na baterii nemění.")
    return status


def _firmware_value(setting: str, value: str) -> tuple[str, str, str]:
    setting = setting.strip().lower()
    value = str(value).strip()

    if setting == "sensitivity":
        choices = {"low": "L", "medium": "M", "high": "H", "l": "L", "m": "M", "h": "H"}
        key = value.lower()
        if key not in choices:
            raise ValueError("Citlivost musí být Low, Medium nebo High.")
        expected = {"L": "LOW", "M": "MEDIUM", "H": "HIGH"}[choices[key]]
        return "7", choices[key], expected

    if setting == "alarm":
        choices = {"enabled": "E", "disabled": "D", "on": "E", "off": "D", "e": "E", "d": "D"}
        key = value.lower()
        if key not in choices:
            raise ValueError("Alarm musí být Enabled nebo Disabled.")
        expected = "ENABLED" if choices[key] == "E" else "DISABLED"
        return "6", choices[key], expected

    if setting in {"low_transfer", "high_transfer"}:
        if not re.fullmatch(r"\d{2,3}", value):
            raise ValueError("Přepínací napětí musí být celé číslo.")
        voltage = int(value)
        if not 80 <= voltage <= 300:
            raise ValueError("Přepínací napětí musí být v rozsahu 80–300 V.")
        menu = "8" if setting == "low_transfer" else "9"
        label = "LOW" if setting == "low_transfer" else "HIGH"
        return menu, str(voltage), f"{label}:{voltage}"

    if setting == "battery_date":
        try:
            parsed = dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Datum baterie musí být ve formátu RRRR-MM-DD.") from exc
        today = dt.date.today()
        if parsed.year < 1980 or parsed > today:
            raise ValueError("Datum baterie musí být mezi rokem 1980 a dneškem.")
        return "4", parsed.strftime("%m/%d/%Y"), parsed.strftime("%m/%d/%Y")

    raise ValueError("Nepovolené nastavení firmware UPS.")


def apply_firmware_setting(setting: str, value: str) -> dict[str, str]:
    menu, apctest_value, expected = _firmware_value(setting, value)

    with _WRITE_LOCK:
        before = _require_usb_online()
        was_active = subprocess.run(
            ["/bin/systemctl", "is-active", "--quiet", "apcupsd.service"],
            check=False,
            timeout=5,
        ).returncode == 0

        if not Path(APCTEST).exists():
            raise RuntimeError("Na Raspberry chybí příkaz apctest.")

        # Each firmware item is changed in its own apctest run. For transfer
        # voltages an extra 0 safely exits the retry prompt if the UPS rejects
        # an unsupported voltage.
        if setting in {"low_transfer", "high_transfer"}:
            script = f"{menu}\n{apctest_value}\n0\nQ\n"
        else:
            script = f"{menu}\n{apctest_value}\nQ\n"

        output = ""
        try:
            subprocess.run(["/bin/systemctl", "stop", "apcupsd.service"], check=True, timeout=15)
            process = subprocess.run(
                [APCTEST],
                input=script,
                capture_output=True,
                text=True,
                timeout=35,
                check=False,
            )
            output = (process.stdout or "") + "\n" + (process.stderr or "")
            if process.returncode != 0:
                raise RuntimeError(f"apctest skončil s chybou {process.returncode}.")

            lowered = output.lower()
            bad_markers = (
                "i don't know how",
                "does not support",
                "failed to set",
                "failed\n",
                "timeout\n",
                "cannot",
                "error opening",
            )
            if any(marker in lowered for marker in bad_markers):
                raise RuntimeError("UPS tuto změnu odmítla nebo ji firmware nepodporuje.")

            if setting == "sensitivity" and f"new sensitivity setting: {expected.lower()}" not in lowered:
                raise RuntimeError("Citlivost se nepodařilo potvrdit zpětným čtením UPS.")
            if setting == "alarm" and f"new alarm setting: {expected.lower()}" not in lowered:
                raise RuntimeError("Nastavení alarmu se nepodařilo potvrdit zpětným čtením UPS.")
            if setting == "battery_date" and expected not in output:
                raise RuntimeError("Datum baterie se nepodařilo potvrdit zpětným čtením UPS.")
            if setting in {"low_transfer", "high_transfer"}:
                label, voltage = expected.split(":", 1)
                confirmation = f"New {label} transfer voltage setting: {voltage} Volts"
                if confirmation.lower() not in lowered:
                    raise RuntimeError("UPS hodnotu přepínacího napětí nepřijala; použij hodnotu podporovanou firmwarem.")
        finally:
            if was_active:
                subprocess.run(["/bin/systemctl", "start", "apcupsd.service"], check=False, timeout=20)

        if was_active:
            for _ in range(20):
                if subprocess.run(
                    ["/bin/systemctl", "is-active", "--quiet", "apcupsd.service"],
                    check=False,
                    timeout=5,
                ).returncode == 0:
                    try:
                        after = _parse_status()
                        return {
                            "setting": setting,
                            "before": before.get("STATUS", ""),
                            "status": after.get("STATUS", ""),
                            "value": value,
                        }
                    except Exception:
                        pass
                time.sleep(0.5)
            raise RuntimeError("Nastavení UPS proběhlo, ale apcupsd se nepodařilo znovu ověřit.")

        return {"setting": setting, "before": before.get("STATUS", ""), "status": "", "value": value}


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
            elif action == "firmware_apply":
                result = apply_firmware_setting(
                    str(request.get("setting", "")),
                    str(request.get("value", "")),
                )
                response = {"ok": True, "firmware": result}
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

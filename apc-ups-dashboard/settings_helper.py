#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

CONFIG = Path("/etc/apcupsd/apcupsd.conf")
ALLOWED = {
    "BATTERYLEVEL": ("int", 0, 100),
    "MINUTES": ("int", 0, 120),
    "TIMEOUT": ("int", 0, 86400),
    "ANNOY": ("int", 0, 86400),
    "ANNOYDELAY": ("int", 0, 86400),
    "NOLOGON": ("choice", {"disable", "timeout", "percent", "minutes", "always"}),
}


def validate(key: str, value: str) -> str:
    spec = ALLOWED[key]
    if spec[0] == "int":
        if not re.fullmatch(r"\d+", value):
            raise ValueError(f"{key}: očekává se celé nezáporné číslo")
        number = int(value)
        if not spec[1] <= number <= spec[2]:
            raise ValueError(f"{key}: povolený rozsah je {spec[1]}–{spec[2]}")
        return str(number)
    if value not in spec[1]:
        raise ValueError(f"{key}: nepovolená hodnota")
    return value


def update_config(changes: dict[str, str]) -> None:
    text = CONFIG.read_text(encoding="utf-8")
    original = text
    lines = text.splitlines()
    found: set[str] = set()

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        if len(parts) == 2 and parts[0] in changes:
            key = parts[0]
            lines[i] = f"{key} {changes[key]}"
            found.add(key)

    for key, value in changes.items():
        if key not in found:
            lines.append(f"{key} {value}")

    new_text = "\n".join(lines) + "\n"
    if new_text == original:
        return

    backup = CONFIG.with_suffix(CONFIG.suffix + ".dashboard.bak")
    shutil.copy2(CONFIG, backup)

    fd, tmp_name = tempfile.mkstemp(prefix="apcupsd.conf.", dir=str(CONFIG.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(new_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, CONFIG.stat().st_mode & 0o777)
        os.replace(tmp_name, CONFIG)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def main() -> None:
    parser = argparse.ArgumentParser()
    for key in ALLOWED:
        parser.add_argument(f"--{key.lower()}")
    args = parser.parse_args()

    changes: dict[str, str] = {}
    for key in ALLOWED:
        value = getattr(args, key.lower())
        if value is not None:
            changes[key] = validate(key, value)

    if not changes:
        raise SystemExit("Nebyla zadána žádná změna.")

    update_config(changes)
    subprocess.run(["/bin/systemctl", "restart", "apcupsd.service"], check=True, timeout=15)
    subprocess.run(["/bin/systemctl", "is-active", "--quiet", "apcupsd.service"], check=True, timeout=5)
    print("OK")


if __name__ == "__main__":
    main()

"""Read-only Raspberry Pi / Linux host and Docker telemetry."""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path

import psutil


def _temperature():
    """Prefer Raspberry Pi's thermal sysfs; gracefully handle non-Pi hosts."""
    candidates = [Path('/sys/class/thermal/thermal_zone0/temp')]
    for path in candidates:
        try:
            value = float(path.read_text().strip())
            return round(value / 1000 if value > 250 else value, 1)
        except (OSError, ValueError):
            pass
    try:
        all_temps = psutil.sensors_temperatures()
        for key in ('cpu_thermal', 'soc_thermal', 'coretemp'):
            if all_temps.get(key):
                return round(float(all_temps[key][0].current), 1)
    except (AttributeError, OSError, ValueError, KeyError):
        pass
    return None


def _disk_snapshot():
    paths = [p.strip() for p in os.getenv('MONITOR_DISKS', '/,/srv/km-cloud,/mnt/sd-backup').split(',') if p.strip()]
    result = []
    for path in dict.fromkeys(paths):
        if not os.path.ismount(path) and path != '/':
            continue  # Do not report unmounted data volumes as if they were mounted.
        try:
            d = psutil.disk_usage(path)
            result.append({'path': path, 'total': d.total, 'used': d.used,
                           'free': d.free, 'percent': d.percent})
        except (OSError, PermissionError):
            continue
    return result


def _docker_rows():
    if shutil.which('docker') is None:
        return [], 'Docker CLI není nainstalováno'
    try:
        ps = subprocess.run(
            ['docker', 'ps', '--format', '{{json .}}'],
            capture_output=True, text=True, timeout=5, check=True,
        )
        stats = subprocess.run(
            ['docker', 'stats', '--no-stream', '--format', '{{json .}}'],
            capture_output=True, text=True, timeout=8, check=True,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [], f'Docker není dostupný: {type(exc).__name__}'
    except subprocess.CalledProcessError as exc:
        # Never expose Docker subprocess stderr; it can contain host paths.
        return [], f'Nelze číst Docker (kód {exc.returncode}); zkontroluj oprávnění služby'

    def parse_lines(s):
        out = []
        for line in s.splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    status = {v.get('Names'): v.get('Status', '') for v in parse_lines(ps.stdout)}
    rows = []
    for item in parse_lines(stats.stdout):
        name = item.get('Name', '?')
        try:
            cpu = float(str(item.get('CPUPerc', '0')).strip('%').replace(',', '.'))
        except ValueError:
            cpu = 0.0
        try:
            mem_pct = float(str(item.get('MemPerc', '0')).strip('%').replace(',', '.'))
        except ValueError:
            mem_pct = 0.0
        rows.append({
            'name': name,
            'cpu': round(cpu, 2),
            'mem_percent': round(mem_pct, 2),
            'memory': str(item.get('MemUsage', '—')).split(' / ')[0],
            'status': status.get(name, 'Running'),
            'pids': str(item.get('PIDs', '—')),
            'net_io': item.get('NetIO', '—'),
        })
    return sorted(rows, key=lambda v: v['cpu'], reverse=True), None


class Collector:
    def __init__(self):
        self.previous_net = None
        self.previous_time = None
        self.last_docker = ([], None)
        self.last_docker_at = 0.0
        psutil.cpu_percent(percpu=True, interval=None)  # Prime per-core counters.

    def take(self):
        now = time.time()
        cores = psutil.cpu_percent(percpu=True, interval=0.2)
        cpu = round(sum(cores) / len(cores), 1) if cores else 0.0
        vm = psutil.virtual_memory()
        rates = {'down': 0.0, 'up': 0.0}
        counters = psutil.net_io_counters()
        if counters and self.previous_net and self.previous_time and now > self.previous_time:
            delta = now - self.previous_time
            rates = {
                'down': round(max(0, counters.bytes_recv - self.previous_net.bytes_recv) / delta, 1),
                'up': round(max(0, counters.bytes_sent - self.previous_net.bytes_sent) / delta, 1),
            }
        self.previous_net, self.previous_time = counters, now
        try:
            load = os.getloadavg()
        except (AttributeError, OSError):
            load = (0, 0, 0)
        if now - self.last_docker_at >= 30:
            self.last_docker = _docker_rows()
            self.last_docker_at = now
        containers, docker_error = self.last_docker
        return {
            'ts': int(now), 'hostname': socket.gethostname(),
            'system': f'{platform.system()} {platform.release()}',
            'cpu': {'percent': cpu, 'cores': [round(c, 1) for c in cores],
                    'load': [round(x, 2) for x in load]},
            'memory': {'total': vm.total, 'used': vm.used, 'available': vm.available,
                       'percent': vm.percent},
            'swap': {'total': psutil.swap_memory().total, 'percent': psutil.swap_memory().percent},
            'temperature': _temperature(),
            'uptime_seconds': int(now - psutil.boot_time()),
            'disks': _disk_snapshot(),
            'network': rates,
            'containers': containers, 'docker_error': docker_error,
        }


def active_alerts(sample):
    limits = {
        'cpu': float(os.getenv('ALERT_CPU', '90')),
        'ram': float(os.getenv('ALERT_RAM', '90')),
        'temperature': float(os.getenv('ALERT_TEMP_C', '78')),
        'disk': float(os.getenv('ALERT_DISK', '90')),
    }
    alerts = []
    if sample['cpu']['percent'] >= limits['cpu']:
        alerts.append({'id': 'cpu', 'title': 'Vysoké zatížení CPU',
                       'detail': f"CPU {sample['cpu']['percent']} %"})
    if sample['memory']['percent'] >= limits['ram']:
        alerts.append({'id': 'ram', 'title': 'Málo dostupné paměti',
                       'detail': f"Využití RAM {sample['memory']['percent']} %"})
    if sample['temperature'] is not None and sample['temperature'] >= limits['temperature']:
        alerts.append({'id': 'temperature', 'title': 'Vysoká teplota procesoru',
                       'detail': f"Teplota {sample['temperature']} °C"})
    for disk in sample['disks']:
        if disk['percent'] >= limits['disk']:
            alerts.append({'id': 'disk:' + disk['path'], 'title': 'Disk téměř zaplněn',
                           'detail': f"{disk['path']}: {disk['percent']} %"})
    return alerts

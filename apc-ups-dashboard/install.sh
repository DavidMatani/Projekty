#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "Spusť: sudo bash install.sh"; exit 1; fi
APP=/opt/apc-ups-dashboard
DATA=/var/lib/apc-ups-dashboard
SRC="$(cd "$(dirname "$0")" && pwd)"
command -v apcaccess >/dev/null || { echo "Chybí apcupsd/apcaccess"; exit 1; }
apt-get update
apt-get install -y python3-venv nginx
mkdir -p "$APP" "$DATA"
install -m 0644 "$SRC/app.py" "$APP/app.py"
chown -R ubuntu:ubuntu "$APP" "$DATA"
sudo -u ubuntu python3 -m venv "$APP/.venv"
sudo -u ubuntu "$APP/.venv/bin/pip" install --upgrade pip
sudo -u ubuntu "$APP/.venv/bin/pip" install Flask==3.1.2 gunicorn==23.0.0
cat >/etc/systemd/system/apc-ups-dashboard.service <<'EOF'
[Unit]
Description=APC UPS Dashboard
After=network-online.target apcupsd.service
Wants=network-online.target
Requires=apcupsd.service
[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/apc-ups-dashboard
Environment=UPS_DASHBOARD_DATA=/var/lib/apc-ups-dashboard
Environment=UPS_POLL_SECONDS=30
Environment=UPS_RETENTION_DAYS=30
ExecStart=/opt/apc-ups-dashboard/.venv/bin/gunicorn --bind 127.0.0.1:8011 --workers 1 --threads 4 --timeout 30 app:app
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/apc-ups-dashboard
[Install]
WantedBy=multi-user.target
EOF
cat >/etc/nginx/sites-available/apc-ups-dashboard <<'EOF'
server {
    listen 80;
    listen [::]:80;
    server_name ups.salonkmbeauty.eu;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    location / {
        proxy_pass http://127.0.0.1:8011;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
ln -sfn /etc/nginx/sites-available/apc-ups-dashboard /etc/nginx/sites-enabled/apc-ups-dashboard
systemctl daemon-reload
systemctl enable --now apc-ups-dashboard
nginx -t
systemctl reload nginx
printf '\nDashboard nainstalován. Test:\n  curl http://127.0.0.1:8011/healthz\n\n'

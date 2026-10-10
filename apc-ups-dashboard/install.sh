#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Spusť: sudo bash install.sh"
  exit 1
fi

APP=/opt/apc-ups-dashboard
DATA=/var/lib/apc-ups-dashboard
ENV_FILE=/etc/apc-ups-dashboard.env
HELPER=/usr/local/sbin/apc-ups-dashboard-apply
SUDOERS=/etc/sudoers.d/apc-ups-dashboard
SRC="$(cd "$(dirname "$0")" && pwd)"
RESET_PASSWORD=0
[[ "${1:-}" == "--reset-password" ]] && RESET_PASSWORD=1

command -v apcaccess >/dev/null || { echo "Chybí apcupsd/apcaccess"; exit 1; }

apt-get update
apt-get install -y python3-venv nginx sudo

if ! id -u upsdash >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin upsdash
fi

mkdir -p "$APP" "$DATA"
install -o root -g root -m 0644 "$SRC/app.py" "$APP/app.py"
install -o root -g root -m 0755 "$SRC/settings_helper.py" "$HELPER"
chown -R upsdash:upsdash "$DATA"

if [[ ! -x "$APP/.venv/bin/python" ]]; then
  python3 -m venv "$APP/.venv"
fi
"$APP/.venv/bin/pip" install --upgrade pip
"$APP/.venv/bin/pip" install Flask==3.1.2 gunicorn==23.0.0

setup_password() {
  local admin password confirm hash secret
  read -r -p "Jméno správce UPS [admin]: " admin
  admin="${admin:-admin}"

  while true; do
    read -r -s -p "Heslo správce UPS (min. 10 znaků): " password
    echo
    if [[ ${#password} -lt 10 ]]; then
      echo "Heslo musí mít alespoň 10 znaků."
      continue
    fi
    read -r -s -p "Heslo znovu: " confirm
    echo
    [[ "$password" == "$confirm" ]] && break
    echo "Hesla se neshodují."
  done

  hash="$("$APP/.venv/bin/python" -c 'from werkzeug.security import generate_password_hash; import sys; print(generate_password_hash(sys.argv[1]))' "$password")"
  secret="$("$APP/.venv/bin/python" -c 'import secrets; print(secrets.token_hex(32))')"

  umask 077
  cat >"$ENV_FILE" <<EOF
UPS_ADMIN_USER=$admin
UPS_ADMIN_PASSWORD_HASH='$hash'
UPS_DASHBOARD_SECRET_KEY=$secret
UPS_ADMIN_SESSION_MINUTES=30
EOF
  chmod 600 "$ENV_FILE"
  chown root:root "$ENV_FILE"
}

if [[ ! -f "$ENV_FILE" || "$RESET_PASSWORD" -eq 1 ]]; then
  setup_password
else
  echo "Administrátorské heslo zůstává zachované."
  echo "Pro změnu: sudo bash install.sh --reset-password"
fi

cat >"$SUDOERS" <<'EOF'
upsdash ALL=(root) NOPASSWD: /usr/local/sbin/apc-ups-dashboard-apply *
EOF
chmod 440 "$SUDOERS"
visudo -cf "$SUDOERS"

cat >/etc/systemd/system/apc-ups-dashboard.service <<'EOF'
[Unit]
Description=APC UPS Dashboard
After=network-online.target apcupsd.service
Wants=network-online.target
Requires=apcupsd.service

[Service]
Type=simple
User=upsdash
Group=upsdash
WorkingDirectory=/opt/apc-ups-dashboard
EnvironmentFile=/etc/apc-ups-dashboard.env
Environment=UPS_DASHBOARD_DATA=/var/lib/apc-ups-dashboard
Environment=UPS_POLL_SECONDS=30
Environment=UPS_RETENTION_DAYS=30
ExecStart=/opt/apc-ups-dashboard/.venv/bin/gunicorn --bind 127.0.0.1:8011 --workers 1 --threads 4 --timeout 30 app:app
Restart=on-failure
RestartSec=3
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/apc-ups-dashboard /etc/apcupsd

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
systemctl enable apc-ups-dashboard
systemctl restart apc-ups-dashboard
nginx -t
systemctl reload nginx

printf '\nDashboard nainstalován.\n'
printf 'Test: curl http://127.0.0.1:8011/healthz\n'
printf 'Nastavení: https://ups.salonkmbeauty.eu/admin\n'

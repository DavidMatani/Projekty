#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Spusť: sudo bash install.sh"
  exit 1
fi

APP=/opt/apc-ups-dashboard
DATA=/var/lib/apc-ups-dashboard
ENV_FILE=/etc/apc-ups-dashboard.env
BRIDGE=/usr/local/lib/apc-ups-dashboard/settings_bridge.py
BRIDGE_SERVICE=/etc/systemd/system/apc-ups-settings-bridge.service
APC_CONF=/etc/apcupsd/apcupsd.conf
SRC="$(cd "$(dirname "$0")" && pwd)"
RESET_PASSWORD=0
[[ "${1:-}" == "--reset-password" ]] && RESET_PASSWORD=1

for cmd in docker apcaccess systemctl nginx python3; do
  command -v "$cmd" >/dev/null || { echo "Chybí příkaz: $cmd"; exit 1; }
done
docker compose version >/dev/null 2>&1 || { echo "Chybí Docker Compose plugin (docker compose)."; exit 1; }

echo "==> Zastavuji starou nekontejnerovou verzi dashboardu, pokud existuje"
systemctl disable --now apc-ups-dashboard.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/apc-ups-dashboard.service

echo "==> Kopíruji Docker verzi dashboardu"
mkdir -p "$APP" "$DATA" "$(dirname "$BRIDGE")"
install -o root -g root -m 0644 "$SRC/app.py" "$APP/app.py"
install -o root -g root -m 0644 "$SRC/Dockerfile" "$APP/Dockerfile"
install -o root -g root -m 0644 "$SRC/compose.yaml" "$APP/compose.yaml"
install -o root -g root -m 0755 "$SRC/settings_bridge.py" "$BRIDGE"
chown -R 10001:10001 "$DATA"

echo "==> Nastavuji apcupsd pro lokální přístup Docker kontejneru"
cp -a "$APC_CONF" "$APC_CONF.before-dashboard-docker.bak"
python3 - "$APC_CONF" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
wanted = {"NETSERVER": "on", "NISIP": "127.0.0.1", "NISPORT": "3551"}
lines = path.read_text(encoding="utf-8").splitlines()
seen = set()
for i, line in enumerate(lines):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        continue
    parts = stripped.split(None, 1)
    if len(parts) == 2 and parts[0] in wanted:
        key = parts[0]
        lines[i] = f"{key} {wanted[key]}"
        seen.add(key)
for key, value in wanted.items():
    if key not in seen:
        lines.append(f"{key} {value}")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
systemctl restart apcupsd.service
systemctl is-active --quiet apcupsd.service

echo "==> Sestavuji Docker image"
docker compose -f "$APP/compose.yaml" build

setup_password() {
  local admin password confirm hash secret bridge_token
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

  hash="$(docker run --rm --entrypoint python apc-ups-dashboard:2.5 -c 'from werkzeug.security import generate_password_hash; import sys; print(generate_password_hash(sys.argv[1]))' "$password")"
  secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  bridge_token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"

  umask 077
  cat >"$ENV_FILE" <<EOF
UPS_ADMIN_USER=$admin
UPS_ADMIN_PASSWORD_HASH='$hash'
UPS_DASHBOARD_SECRET_KEY=$secret
UPS_ADMIN_SESSION_MINUTES=30
UPS_BRIDGE_TOKEN=$bridge_token
EOF
  chmod 600 "$ENV_FILE"
  chown root:root "$ENV_FILE"
}

if [[ ! -f "$ENV_FILE" || "$RESET_PASSWORD" -eq 1 ]] || ! grep -q '^UPS_ADMIN_PASSWORD_HASH=' "$ENV_FILE"; then
  setup_password
else
  echo "==> Administrátorské heslo zůstává zachované"
  if ! grep -q '^UPS_DASHBOARD_SECRET_KEY=' "$ENV_FILE"; then
    echo "UPS_DASHBOARD_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" >>"$ENV_FILE"
  fi
  if ! grep -q '^UPS_ADMIN_SESSION_MINUTES=' "$ENV_FILE"; then
    echo "UPS_ADMIN_SESSION_MINUTES=30" >>"$ENV_FILE"
  fi
  if ! grep -q '^UPS_BRIDGE_TOKEN=' "$ENV_FILE"; then
    echo "UPS_BRIDGE_TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" >>"$ENV_FILE"
  fi
  chmod 600 "$ENV_FILE"
  chown root:root "$ENV_FILE"
fi

echo "==> Instaluji omezenou hostitelskou službu pro změny nastavení"
cat >"$BRIDGE_SERVICE" <<EOF
[Unit]
Description=APC UPS Dashboard Settings Bridge
After=apcupsd.service
Wants=apcupsd.service
Before=docker.service

[Service]
Type=simple
User=root
Group=root
EnvironmentFile=$ENV_FILE
Environment=UPS_BRIDGE_GID=10001
Environment=UPS_APC_CONFIG_HOST=$APC_CONF
Environment=UPS_EVENTS_FILE_HOST=/var/log/apcupsd.events
Environment=UPS_SETTINGS_SOCKET=/run/apc-ups-dashboard/settings.sock
ExecStart=$BRIDGE
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/etc/apcupsd /run/apc-ups-dashboard
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

touch /var/log/apcupsd.events
systemctl daemon-reload
systemctl enable --now apc-ups-settings-bridge.service

echo "==> Spouštím dashboard v Dockeru"
docker compose -f "$APP/compose.yaml" up -d --remove-orphans

echo "==> Nastavuji Nginx"
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
nginx -t
systemctl reload nginx

echo
echo "Hotovo."
echo "Kontejner:"
docker ps --filter name=apc-ups-dashboard
echo
echo "Test:"
echo "  curl http://127.0.0.1:8011/healthz"
echo "Nastavení:"
echo "  https://ups.salonkmbeauty.eu/admin"
echo
echo "Změna hesla později:"
echo "  sudo bash install.sh --reset-password"

# Pi Monitor — dashboard pro Raspberry Pi

Moderní, responzivní webová aplikace pro průběžné sledování **Raspberry Pi 4 / Ubuntu**. Zobrazuje data skutečného hostitele, nikoliv jen izolovaného Docker kontejneru.

## Funkce

- Živé vytížení CPU a jednotlivých jader, load average a uptime.
- RAM včetně dostupné paměti a swap, teplota procesoru.
- Úložiště: systémový disk, KM Cloud a záložní SD karta (jen pokud jsou skutečně připojené).
- Docker: seznam běžících kontejnerů, CPU, RAM, procesy a stav.
- Síťový přenos, automatické aktualizace a historické grafy **1 hodina / 24 hodin / 7 dní**.
- SQLite historie ukládaná každých 20 s, automatické promazávání po 8 dnech.
- Upozornění při překročení mezí CPU, RAM, teploty a obsazenosti disků, historie přechodů do výstrahy a zpět (30 dní). Upozornění jsou **na webu**, nikoliv push/e-mailem.
- Přihlášení heslem a omezení počtu neúspěšných pokusů. Dashboard a API jsou neveřejné.
- Měření funguje i bez Dockeru; v takovém případě se zobrazí informativní hláška.

## Instalace na Ubuntu Raspberry Pi (doporučeno jako systemd služba)

Aplikace je uložena ve složce `raspberry-monitor` repozitáře `DavidMatani/Projekty`, společně s dalšími samostatnými aplikacemi.

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip
sudo mkdir -p /opt /var/lib/raspberry-monitor
sudo chown ubuntu:ubuntu /var/lib/raspberry-monitor
cd /opt
sudo git clone https://github.com/DavidMatani/Projekty.git
sudo chown -R ubuntu:ubuntu /opt/Projekty
cd /opt/Projekty/raspberry-monitor
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Pokud repozitář v `/opt/Projekty` už existuje, místo klonování použij `cd /opt/Projekty && git pull --ff-only`.

### Konfigurace a heslo

Pro vytvoření nastavení:

```bash
cd /opt/Projekty/raspberry-monitor
sudo cp .env.example /etc/raspberry-monitor.env
sudo chmod 600 /etc/raspberry-monitor.env
# Bezpečné hodnoty si nech vygenerovat přímo na Pi:
python3 -c 'import secrets; print("MONITOR_PASSWORD="+secrets.token_urlsafe(24)); print("FLASK_SECRET_KEY="+secrets.token_urlsafe(48))'
sudo nano /etc/raspberry-monitor.env
```

Nahraď `MONITOR_PASSWORD` a `FLASK_SECRET_KEY` v `/etc/raspberry-monitor.env` vygenerovanými hodnotami. **Nikdy je neukládej na GitHub.** Zvol jiné heslo než pro ostatní služby.

Pro lokální přístup přes SSH tunel nastav `COOKIE_SECURE=0`; pro přístup přes HTTPS na doméně nastav `COOKIE_SECURE=1` (doporučeno) a ponech `TRUST_PROXY=0`, pokud není nutné předávat hlavičky z důvěryhodné reverzní proxy.

### Přístup k Docker statistikám

Služba standardně běží jako neprivilegovaný uživatel `ubuntu`. Pro zobrazování Docker kontejnerů potřebuje **oprávnění k Docker daemonu**. Nejdříve vyzkoušej:

```bash
sudo -u ubuntu docker ps
```

Pokud to vrátí chybu oprávnění, lze uživatele přidat do skupiny `docker`:

```bash
sudo usermod -aG docker ubuntu
```

**Bezpečnostní upozornění:** členství ve skupině `docker` poskytuje v praxi oprávnění srovnatelná s rootem. Pokud tuto úroveň oprávnění nechceš uživateli udělit, nech Docker metriky vypnuté; ostatní sledování bude nadále fungovat. Nezpřístupňuj Docker socket veřejně, nesdílej hesla a Docker socket nepřipojuj do veřejně dostupného kontejneru.

### Spuštění služby

```bash
cd /opt/Projekty/raspberry-monitor
sudo cp deploy/raspberry-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now raspberry-monitor
sudo systemctl status raspberry-monitor --no-pager
curl -s http://127.0.0.1:8003/healthz
```

Služba poslouchá **jen na `127.0.0.1:8003`**, aby nebyla bez konfigurace přístupná z internetu či lokální sítě.

### Otevření v prohlížeči

Bez změny Nginxu lze z PC vytvořit SSH tunel:

```bash
ssh -L 8003:127.0.0.1:8003 ubuntu@IP_RASPBERRY_PI
```

Potom otevři `http://127.0.0.1:8003` a přihlas se. Pro tunel je nutné `COOKIE_SECURE=0`, protože jde o lokální HTTP. **Z jiného zařízení/domény použij HTTPS přes Nginx/Cloudflare Tunnel a nastav `COOKIE_SECURE=1`.** Novou doménu a Cloudflare route záměrně nekonfigurujeme automaticky, abychom nerozbili stávající služby.

### Aktualizace

```bash
cd /opt/Projekty
git pull --ff-only
cd raspberry-monitor
.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart raspberry-monitor
```

Logy: `sudo journalctl -u raspberry-monitor -n 100 --no-pager`.

## Nastavení

| Proměnná | Význam / výchozí hodnota |
|---|---|
| `MONITOR_USER` | Povinné uživatelské jméno |
| `MONITOR_PASSWORD` | Povinné heslo, min. 12 znaků |
| `FLASK_SECRET_KEY` | Povinný náhodný klíč, min. 32 znaků |
| `MONITOR_DB` | `/var/lib/raspberry-monitor/metrics.sqlite3` |
| `MONITOR_DISKS` | `/,/srv/km-cloud,/mnt/sd-backup` |
| `SAMPLE_INTERVAL` | Interval sběru dat v s (min. 10, výchozí 20) |
| `ALERT_CPU` | CPU % (90) |
| `ALERT_RAM` | RAM % (90) |
| `ALERT_TEMP_C` | Teplota °C (78) |
| `ALERT_DISK` | Disk % (90) |
| `COOKIE_SECURE` | `1` = cookie pouze přes HTTPS (výchozí) |
| `TRUST_PROXY` | `1` = důvěřovat jedné reverzní proxy; zapnout pouze za proxy |

## Testy a kontrola

```bash
.venv/bin/python -m pip install pytest
MONITOR_SKIP_AUTOSTART=1 .venv/bin/python -m pytest -q
```

Očekávané endpointy: `/healthz` (bez citlivých dat), `/login`, `/`, `/api/live`, `/api/history?range=24h`. Aplikace pouze **čte** metriky a Docker statistiky. Nemůže startovat, zastavovat ani měnit kontejnery nebo nastavení systému. Všechny dostupné citlivější údaje jsou pouze za přihlášením.

## Provozní poznámky

- **Pouze jeden Gunicorn worker:** jeden background sampler na worker; pro více workerů by bylo nutné oddělit sběrač dat do vlastní služby.
- Historie před první instalací neexistuje. Sedmidenní přehled se naplní postupně během týdne.
- CPU měření a Docker statistics jsou okamžité snímky, nikoliv konstantní spotřeba.
- Graf teploty používá stupnici 0–100 °C; CPU/RAM stupnici 0–100 %.
- Z bezpečnostních důvodů GitHub neobsahuje databáze, hesla ani konfiguraci Cloudflare.

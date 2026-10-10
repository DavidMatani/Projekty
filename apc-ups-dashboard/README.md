# APC UPS Dashboard

Grafický dashboard pro APC Back-UPS připojenou k Raspberry Pi přes `apcupsd`.

Zobrazuje aktuální stav **ONLINE / NA BATERII**, nabití baterie, odhad výdrže, zatížení v % a W, vstupní napětí, napětí baterie, datum baterie, důvod posledního přechodu a bezpečnostní limity. Každých 30 sekund ukládá vzorek do SQLite a zobrazuje graf baterie a zatížení za posledních 24 hodin.

Dashboard pouze čte data. Bezpečné vypnutí Raspberry při výpadku dál zajišťuje `apcupsd`.

## Instalace na Raspberry Pi

Nejdřív ověř:

```bash
apcaccess status
```

Potom v této složce spusť:

```bash
sudo bash install.sh
```

Aplikace poběží lokálně na:

```text
127.0.0.1:8011
```

Nginx konfigurace je připravena pro:

```text
ups.salonkmbeauty.eu
```

## Kontrola

```bash
sudo systemctl status apc-ups-dashboard --no-pager
curl http://127.0.0.1:8011/healthz
```

Log:

```bash
journalctl -u apc-ups-dashboard -f
```

## Data

Historie se ukládá do:

```text
/var/lib/apc-ups-dashboard/ups_history.sqlite3
```

Výchozí uchování historie je 30 dní. Web zobrazuje posledních 24 hodin.

## Aktualizace

Po stažení novějšího `app.py`:

```bash
sudo cp app.py /opt/apc-ups-dashboard/app.py
sudo chown ubuntu:ubuntu /opt/apc-ups-dashboard/app.py
sudo systemctl restart apc-ups-dashboard
```

## Cloudflare

Až bude služba fungovat lokálně, je možné v existujícím Cloudflare Tunnelu přidat hostname `ups.salonkmbeauty.eu` směřující na `http://localhost:80`. Pro veřejné vystavení je vhodné přidat autentizaci přes Cloudflare Access.

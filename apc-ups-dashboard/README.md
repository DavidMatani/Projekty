# APC UPS Dashboard

Webový dashboard pro APC Back-UPS připojenou k Raspberry Pi přes `apcupsd`.

## Co zobrazuje

Dashboard čte kompletní výstup `apcaccess status` a zobrazuje:

- stav **ONLINE / NA BATERII / NÍZKÁ BATERIE**,
- nabití baterie, napětí baterie a odhad výdrže,
- zatížení UPS v % a přibližný výkon ve wattech,
- síťové napětí a dolní/horní přepínací mez,
- citlivost UPS,
- počet přechodů na baterii,
- aktuální i kumulativní čas na baterii,
- důvod posledního přechodu,
- výsledek self-testu,
- nastavení alarmu,
- datum baterie,
- firmware, sériové číslo, kabel, driver a režim,
- všechny ostatní položky, které konkrétní UPS vrátí přes `apcaccess`.

Dashboard navíc ukládá historii do SQLite:

- síťové napětí,
- stav baterie,
- odhad výdrže,
- zatížení,
- přibližný výkon,
- napětí baterie,
- začátek a konec výpadků proudu.

Výchozí interval ukládání je 30 sekund a historie se drží 30 dní.

## Nastavení pod heslem

Veřejná část dashboardu je pouze pro čtení. Stránka:

```text
https://ups.salonkmbeauty.eu/admin
```

je chráněná uživatelským jménem a heslem.

Z administrace lze bezpečně měnit vybraná nastavení `apcupsd`:

- `BATTERYLEVEL`,
- `MINUTES`,
- `TIMEOUT`,
- `ANNOY`,
- `ANNOYDELAY`,
- `NOLOGON`.

Po uložení se konfigurace validuje, zálohuje a služba `apcupsd` se restartuje.

Nastavení uložená přímo ve firmware UPS, například citlivost, přepínací napětí, alarm nebo datum baterie, se zobrazují, ale dashboard je automaticky nepřepisuje. Tyto položky se u tohoto typu APC mění přes interaktivní `apctest` a dostupné volby závisejí na konkrétním firmware.

## Instalace / aktualizace

Na Raspberry Pi:

```bash
cd ~/Projekty
git pull
cd apc-ups-dashboard
sudo bash install.sh
```

Při první instalaci nové verze se instalátor zeptá na jméno a heslo správce.

Pro pozdější změnu hesla:

```bash
sudo bash install.sh --reset-password
```

Instalátor vytvoří:

- aplikaci v `/opt/apc-ups-dashboard`,
- historii v `/var/lib/apc-ups-dashboard/history.sqlite3`,
- systemd službu `apc-ups-dashboard.service`,
- chráněný pomocný příkaz pro změnu povolených nastavení,
- Nginx konfiguraci pro `ups.salonkmbeauty.eu`,
- soubor s hashem administrátorského hesla v `/etc/apc-ups-dashboard.env`.

Heslo se neukládá v otevřeném textu.

## Kontrola

```bash
sudo systemctl status apc-ups-dashboard --no-pager
curl http://127.0.0.1:8011/healthz
```

Log:

```bash
journalctl -u apc-ups-dashboard -f
```

## Cloudflare

Existující Cloudflare Tunnel může směrovat hostname:

```text
ups.salonkmbeauty.eu
```

na:

```text
http://localhost:80
```

Pro další ochranu lze před veřejný dashboard přidat Cloudflare Access.

# APC UPS Dashboard

Webový dashboard pro APC Back-UPS připojenou k Raspberry Pi přes `apcupsd`.

## Architektura

Webová aplikace běží v Docker kontejneru `apc-ups-dashboard`.

`apcupsd` zůstává přímo na Raspberry Pi, protože komunikuje s UPS přes USB a zajišťuje bezpečné vypnutí celého serveru. Kontejner se k němu připojuje pouze lokálně přes NIS na `127.0.0.1:3551`.

Změny nastavení z webu předává kontejner malé omezené hostitelské službě `apc-ups-settings-bridge.service` přes Unix socket. Kontejner nemá Docker socket, není privilegovaný a nemá přímý zápis do `/etc/apcupsd`.

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

Dashboard ukládá do SQLite také historii:

- síťového napětí,
- stavu baterie,
- odhadované výdrže,
- zatížení,
- přibližného výkonu,
- napětí baterie,
- začátků a konců výpadků proudu.

Výchozí interval ukládání je 30 sekund a historie se drží 30 dní.

## Nastavení pod heslem

Veřejná část dashboardu je pouze pro čtení. Administrace:

```text
https://ups.salonkmbeauty.eu/admin
```

je chráněná uživatelským jménem a heslem.

Bezpečně lze měnit tyto parametry `apcupsd`:

- `ONBATTERYDELAY`,
- `BATTERYLEVEL`,
- `MINUTES`,
- `TIMEOUT`,
- `KILLDELAY`,
- `ANNOY`,
- `ANNOYDELAY`,
- `NOLOGON`,
- `UPSNAME`,
- `POLLTIME`,
- `EVENTSFILEMAX`,
- `STATTIME`,
- `DATATIME`,
- `LOGSTATS`.

Hodnoty se na hostiteli validují. Před změnou vznikne záloha konfigurace a po změně se restartuje `apcupsd`. Pokud restart selže, bridge vrátí původní konfiguraci.

Síťové parametry `NETSERVER`, `NISIP` a `NISPORT` zůstávají záměrně uzamčené, protože Docker dashboard používá lokální NIS spojení `127.0.0.1:3551`.

Nastavení uložená přímo ve firmware UPS lze v administraci měnit také:

- citlivost **Low / Medium / High**,
- dolní přepínací napětí,
- horní přepínací napětí,
- zvukový alarm **zapnout / vypnout**,
- datum instalace nebo výměny baterie.

Tyto změny provádí hostitelský bridge přes `apctest`. Před zápisem musí být UPS **ONLINE**. Bridge na několik sekund zastaví `apcupsd`, provede právě jednu požadovanou změnu, ověří odpověď UPS a `apcupsd` znovu spustí. U přepínacích napětí se nepodporovaná hodnota nepovažuje za úspěch, protože povolené hodnoty určuje firmware konkrétního modelu. Každá změna v této sekci vyžaduje samostatné potvrzení v administraci.

## Docker

Kontejner používá:

```text
restart: unless-stopped
network_mode: host
read_only: true
cap_drop: ALL
no-new-privileges
```

Historie je mimo kontejner:

```text
/var/lib/apc-ups-dashboard/history.sqlite3
```

Takže při aktualizaci nebo znovuvytvoření kontejneru zůstane zachovaná.

## Instalace / převod ze staré verze

Na Raspberry Pi:

```bash
cd ~/Projekty
git pull
cd apc-ups-dashboard
sudo bash install.sh
```

Instalátor automaticky:

- zastaví a odstraní starou nekontejnerovou službu `apc-ups-dashboard.service`,
- nastaví lokální NIS rozhraní `apcupsd`,
- sestaví image `apc-ups-dashboard:2.3`,
- nainstaluje a spustí `apc-ups-settings-bridge.service`,
- spustí kontejner s `restart: unless-stopped`,
- zachová historii v `/var/lib/apc-ups-dashboard`,
- nastaví Nginx pro `ups.salonkmbeauty.eu`.

Při první instalaci se instalátor zeptá na jméno a heslo správce. Heslo se ukládá pouze jako hash v root-only souboru:

```text
/etc/apc-ups-dashboard.env
```

Pozdější změna hesla:

```bash
sudo bash install.sh --reset-password
```

## Kontrola

Stav kontejneru:

```bash
sudo docker ps --filter name=apc-ups-dashboard
```

Healthcheck:

```bash
curl http://127.0.0.1:8011/healthz
```

Log webu:

```bash
sudo docker logs -f apc-ups-dashboard
```

Stav bridge:

```bash
sudo systemctl status apc-ups-settings-bridge --no-pager
```

Log bridge:

```bash
sudo journalctl -u apc-ups-settings-bridge -f
```

## Cloudflare

Existující Cloudflare Tunnel může dál směrovat:

```text
ups.salonkmbeauty.eu
```

na:

```text
http://localhost:80
```

Nginx pak požadavek předá kontejneru na `127.0.0.1:8011`.

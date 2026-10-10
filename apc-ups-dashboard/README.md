# APC UPS Dashboard

Grafický webový dashboard pro APC Back-UPS připojenou k Raspberry Pi přes `apcupsd`.

Zobrazuje živě:

- stav **ONLINE / NA BATERII**,
- nabití baterie a odhad zbývajícího času,
- zatížení UPS v % a přibližně ve wattech,
- síťové a bateriové napětí,
- datum baterie,
- důvod posledního přechodu na baterii,
- limity bezpečného vypnutí z `apcupsd`.

Data se obnovují každých 5 sekund. Dashboard UPS pouze čte; automatické bezpečné vypnutí Raspberry dál zajišťuje `apcupsd`.

## Instalace na Raspberry Pi

Nejdřív ověř komunikaci:

```bash
apcaccess status
```

Potom v této složce spusť:

```bash
sudo bash install.sh
```

Aplikace poběží na:

```text
http://127.0.0.1:8011
```

Instalátor zároveň vytvoří systemd službu `apc-ups-dashboard` a Nginx konfiguraci pro:

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

## Cloudflare

Až bude dashboard fungovat lokálně, přidej v existujícím Cloudflare Tunnelu hostname `ups.salonkmbeauty.eu` směrovaný na `http://localhost:80`.

Pokud bude dashboard dostupný z internetu, doporučuje se před něj přidat Cloudflare Access.

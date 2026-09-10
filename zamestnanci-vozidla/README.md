# Evidence zaměstnanců a vozidel

Interní webová aplikace ve Flasku pro:

- plánování absencí zaměstnanců (dovolená, lékař, nemoc, školení, náhradní volno, služební cesta, překážka v práci),
- evidenci dokumentů zaměstnanců a jejich platnosti,
- technickou evidenci nákladních vozidel,
- evidenci dokumentů vozidel a jejich platnosti,
- servisní historii a plánované servisní termíny,
- přehled upozornění na blížící se expirace.

Aplikace **neplánuje dopravu ani jízdy vozidel**.

## Raspberry Pi / Docker Compose

Projekt obsahuje `Dockerfile` a `docker-compose.yml`. Compose spouští dva kontejnery:

- `zamestnanci-vozidla-app` – Flask + Gunicorn,
- `zamestnanci-vozidla-db` – PostgreSQL 18.

Web je na hostiteli dostupný pouze na `127.0.0.1:8003`, aby byl publikován až přes reverzní proxy.

### 1. Stažení projektu

```bash
cd /opt
sudo git clone https://github.com/DavidMatani/Projekty.git projekty
sudo chown -R $USER:$USER /opt/projekty
cd /opt/projekty/zamestnanci-vozidla
```

### 2. Trvalé úložiště dokumentů

Aplikace uvnitř kontejneru běží jako UID/GID `10001`.

```bash
sudo install -d -o 10001 -g 10001 -m 700 /srv/zamestnanci-vozidla/uploads
```

Občanské průkazy, řidičské průkazy a další přílohy se ukládají sem a nejsou součástí Docker image ani Git repozitáře.

### 3. Tajné hodnoty

```bash
SECRET=$(openssl rand -hex 48)
DBPASS=$(openssl rand -hex 32)
cat > .env <<EOF
SECRET_KEY=$SECRET
POSTGRES_DB=zamestnanci_vozidla
POSTGRES_USER=zamestnanci
POSTGRES_PASSWORD=$DBPASS
MAX_CONTENT_LENGTH_MB=16
EOF
chmod 600 .env
```

### 4. Spuštění

```bash
docker compose up -d --build
docker compose ps
curl -I http://127.0.0.1:8003/setup
```

Při prvním otevření webu se zobrazí vytvoření prvního správce.

### Aktualizace

```bash
cd /opt/projekty
git pull
cd zamestnanci-vozidla
docker compose up -d --build
```

### Logy

```bash
cd /opt/projekty/zamestnanci-vozidla
docker compose logs -f app
```

### Zastavení / spuštění

```bash
docker compose stop
docker compose start
```

Kontejnery mají `restart: unless-stopped`, takže se po restartu Raspberry Pi automaticky spustí.

## Lokální spuštění bez Dockeru

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Výchozí lokální režim umí SQLite. Na serveru je přes Docker Compose použito PostgreSQL.

## Dokumenty a soukromí

Nahrané občanské průkazy, řidičské průkazy, smlouvy, technické dokumenty a další přílohy jsou ukládány do `uploads/`. Složka je v `.gitignore` i `.dockerignore` a nesmí se commitovat do GitHubu.

## Upozornění

Dashboard zvýrazňuje dokumenty s koncem platnosti do 30 dnů a servisní termíny vozidel.

# Evidence zaměstnanců a vozidel

Interní webová aplikace ve Flasku pro:

- plánování absencí zaměstnanců (dovolená, lékař, nemoc, školení, náhradní volno, služební cesta, překážka v práci),
- evidenci dokumentů zaměstnanců a jejich platnosti,
- technickou evidenci nákladních vozidel,
- evidenci dokumentů vozidel a jejich platnosti,
- servisní historii a plánované servisní termíny,
- přehled upozornění na blížící se expirace.

Aplikace **neplánuje dopravu ani jízdy vozidel**.

## Instalace

```bash
python -m venv .venv
source .venv/bin/activate   # Linux
# .venv\Scripts\activate    # Windows
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Poté otevři `http://127.0.0.1:5000`. Při prvním spuštění se zobrazí stránka pro vytvoření prvního správce.

## Databáze

Výchozí je SQLite pro snadný lokální start. Na serveru lze použít PostgreSQL přes `DATABASE_URL`, například:

```text
postgresql+psycopg://uzivatel:heslo@127.0.0.1:5432/zamestnanci_db
```

## Dokumenty a soukromí

Nahrané občanské průkazy, řidičské průkazy, smlouvy, technické dokumenty a další přílohy jsou ukládány do lokální složky `uploads/`. Tato složka je v `.gitignore` a nesmí se commitovat do GitHubu.

## Upozornění

Dashboard zvýrazňuje dokumenty s koncem platnosti do 30 dnů a servisní termíny vozidel.

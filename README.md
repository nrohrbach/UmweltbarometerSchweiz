# UmweltbarometerSchweiz

Ein täglich automatisch laufendes System, das Schweizer News-RSS-Feeds abfragt, Artikel mittels
KI-Klassifikation (Groq API, `llama-3.3-70b-versatile`, batchweise) rund 50 vordefinierten
Umweltthemen zuordnet und die Ergebnisse (Thema, Datum, Quelle, ggf. geografische Bounding Box)
versioniert in `data/sightings.csv` speichert.

Vollständige technische Spezifikation: [`umwelt_barometer_spec.md`](umwelt_barometer_spec.md).

## Architektur

```
RSS Fetcher → Keyword-Vorfilter → KI-Klassifikation (Groq, Batch) → Geocoding (geo.admin.ch) → Storage (Git)
```

Kein Ziel von v1: Score-Berechnung, Normalisierung, Dashboard/Visualisierung, Abgleich mit
BAFU-Risikoanalyse.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # GROQ_API_KEY eintragen (kostenlos via console.groq.com), .env NIE committen
```

## Lokal ausführen

```bash
export GROQ_API_KEY=...   # oder via .env + python-dotenv
python src/main.py
```

## Repo-Struktur

```
├── .github/workflows/daily_scrape.yml   # täglicher Cron-Job via GitHub Actions
├── config/
│   ├── feeds.yaml                       # RSS-Feed-Liste
│   └── themes.csv                       # Umweltthemen-Taxonomie
├── src/
│   ├── fetch_rss.py                     # Komponente 1: RSS abfragen
│   ├── prefilter.py                     # Komponente 2: Keyword-Vorfilter
│   ├── classify.py                      # Komponente 3: KI-Klassifikation
│   ├── geocode.py                       # Komponente 4: Ortsname → Bounding Box
│   ├── storage.py                       # Komponente 5: Dedupe + Speichern
│   └── main.py                          # Orchestriert 1-5
└── data/sightings.csv                   # wachsende Ergebnis-Datei (Zeitreihe via Git-History)
```

## GitHub Actions

Der Workflow läuft täglich um 05:00 UTC (07:00 MEZ) und kann zusätzlich manuell über
`workflow_dispatch` gestartet werden. Dafür muss im Repo unter Settings → Secrets and
variables → Actions das Secret `GROQ_API_KEY` hinterlegt werden.

# Umwelt-Aufmerksamkeitsbarometer Schweiz — Technische Spezifikation

**Zweck dieses Dokuments:** Diese Spezifikation ist so detailliert geschrieben, dass sie direkt an
Claude Code übergeben werden kann, um das Projekt umzusetzen. Sie beschreibt Architektur,
Datenmodelle, Komponenten und einen Schritt-für-Schritt-Implementierungsplan.

---

## 1. Zielsetzung

Ein täglich automatisch laufendes System, das:
1. RSS-Feeds Schweizer Newsseiten abfragt
2. jeden Artikel mittels KI-Klassifikation einem von ~50 vordefinierten Umweltthemen zuordnet
   (oder keinem)
3. pro Treffer Thema, Datum, Quelle und – wo möglich – eine geografische Bounding Box speichert
4. die Ergebnisse dauerhaft und versioniert speichert (Git-History als Zeitreihe)
5. das Ganze täglich automatisiert via GitHub Actions ausführt, ohne eigene Serverinfrastruktur

**Explizit kein Ziel von v1:** Score-Berechnung (0-100), Normalisierung, Dashboard/Visualisierung,
Abgleich mit BAFU-Risikoanalyse. Das sind spätere Ausbaustufen auf Basis der hier gesammelten
Rohdaten.

---

## 2. Architektur-Übersicht

```
GitHub Actions (täglicher Cron-Trigger)
        │
        ▼
┌─────────────────────┐
│ 1. RSS Fetcher       │  liest RSS_FEEDS, holt aktuelle Artikel (Titel + Summary + Link)
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 2. Keyword-Vorfilter │  grobe Vorauswahl: enthält Artikel überhaupt ein Themen-Stichwort?
└─────────┬───────────┘  (spart spätere API-Kosten, keine Pflicht aber empfohlen)
          ▼
┌─────────────────────┐
│ 3. KI-Klassifikation │  Claude API: ordnet Artikel den Themen aus themes.csv zu,
│    (Anthropic API)   │  extrahiert zusätzlich erwähnte Ortsnamen als Text
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 4. Geocoding         │  GeoAdmin SearchServer: Ortsname → Bounding Box (LV95/WGS84)
│    (geo.admin.ch)    │
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 5. Storage           │  Ergebnisse an sightings.csv anhängen, committen, pushen
│    (Git-History)     │  → jeder Tages-Commit ist ein Snapshot, Git-Log = Zeitreihe
└─────────────────────┘
```

---

## 3. Repo-Struktur

```
umwelt-aufmerksamkeitsbarometer/
├── .github/
│   └── workflows/
│       └── daily_scrape.yml       # GitHub Actions Workflow
├── config/
│   ├── feeds.yaml                 # Liste der RSS-Feed-URLs
│   └── themes.csv                 # Die ~50 Umweltthemen (Titel + Beschreibung)
├── src/
│   ├── fetch_rss.py                # Komponente 1
│   ├── prefilter.py                 # Komponente 2
│   ├── classify.py                  # Komponente 3
│   ├── geocode.py                   # Komponente 4
│   ├── storage.py                   # Komponente 5
│   └── main.py                      # Orchestriert 1-5
├── data/
│   └── sightings.csv                # Ergebnis-Datei (wächst täglich, wird committed)
├── requirements.txt
├── README.md
└── .env.example                     # Vorlage für lokale Tests (nie committen!)
```

---

## 4. Datenmodelle

### 4.1 `config/themes.csv` — Themen-Taxonomie

| Spalte | Typ | Beschreibung |
|---|---|---|
| `theme_id` | string | Eindeutige, stabile ID, z.B. `hitze_gesundheit` (nie ändern, nur ergänzen) |
| `label_de` | string | Anzeigename, z.B. "Hitzebelastung Gesundheit" |
| `description` | string | 1-2 Sätze, die der KI erklären, was unter dieses Thema fällt |
| `bafu_risk_ref` | string (optional) | Referenz auf die Nummer/Bezeichnung aus der BAFU-Klima-Risikoanalyse, falls vorhanden — ermöglicht späteren Abgleich |
| `example_keywords` | string (optional) | Kommagetrennte Beispielwörter, nur für den Keyword-Vorfilter (Komponente 2), NICHT für die KI-Klassifikation massgeblich |

**Empfehlung:** Themen an die 34 Risiken der BAFU-Klima-Risikoanalyse anlehnen (Spalte
`bafu_risk_ref` befüllen), damit später ein Abgleich wissenschaftliche Dringlichkeit vs.
öffentliche Aufmerksamkeit möglich ist, ohne nachträgliches Mapping.

**Startgrösse:** Auch wenn das Ziel 50 Themen sind — für den ersten funktionierenden Durchlauf
mit 10-15 Themen starten, Rest schrittweise ergänzen. Die Themenliste sollte danach über die
Projektlaufzeit stabil bleiben (IDs nie umbenennen, nur neue hinzufügen), sonst werden
Zeitreihen unvergleichbar.

### 4.2 `data/sightings.csv` — Ergebnis-Datei

| Spalte | Typ | Beschreibung |
|---|---|---|
| `date` | date (YYYY-MM-DD) | Tag des RSS-Abrufs (nicht zwingend Publikationsdatum) |
| `theme_id` | string | Referenz auf `themes.csv` |
| `source` | string | z.B. `srf`, `nzz`, `20min` |
| `article_title` | string | Titel des Artikels |
| `article_url` | string | Link zum Original (für Nachvollziehbarkeit, nie Volltext speichern — Copyright) |
| `place_name_raw` | string (optional) | Von der KI extrahierter Ortsname im Originaltext, leer falls keiner erkannt |
| `bbox_wgs84` | string (optional) | Bounding Box als `minLon,minLat,maxLon,maxLat` in WGS84, leer falls Geocoding erfolglos |
| `classification_confidence` | string (optional) | z.B. `high`/`medium`/`low`, falls die KI das mitgibt |

**Wichtig:** Nur Titel speichern, nie den vollen Artikeltext (Copyright — siehe auch die
Diskussion zu Quellenwahl weiter oben im Projekt).

---

## 5. Komponenten im Detail

### 5.1 `fetch_rss.py`

- Liest Feed-Liste aus `config/feeds.yaml`
- Nutzt `feedparser`
- Gibt eine Liste von Dicts zurück: `{title, summary, url, source, published}`
- Kein Datumsfilter nötig — RSS liefert ohnehin nur aktuelle Einträge
- Fehlerbehandlung: einzelner kaputter Feed darf den gesamten Lauf nicht abbrechen (try/except
  pro Feed, Fehler loggen und weitermachen)

**`config/feeds.yaml` Beispielstruktur:**
```yaml
feeds:
  - name: srf
    url: https://www.srf.ch/news/bnf/rss/1890
  - name: nzz
    url: https://www.nzz.ch/recent.rss
  - name: 20min
    url: https://partner-feeds.beta.20min.ch/rss/20minuten/schweiz
  - name: tagesanzeiger
    url: https://partner-feeds.publishing.tamedia.ch/rss/tagesanzeiger/schweiz
  - name: blick
    url: https://www.blick.ch/schweiz/rss.xml
  - name: aargauerzeitung
    url: https://www.aargauerzeitung.ch/schweiz.rss
  - name: letemps
    url: https://www.letemps.ch/suisse.rss          # Französisch
  - name: cdt
    url: https://www.cdt.ch/feed/svizzera             # Italienisch (Corriere del Ticino)
  - name: laregione
    url: https://media.laregione.ch/files/domains/laregione.ch/rss/rss_svizzera.xml  # Italienisch
```

**Hinweis zu Mehrsprachigkeit:** Mit Le Temps, Corriere del Ticino und laRegione sind jetzt auch
französisch- und italienischsprachige Quellen dabei. Das ist unproblematisch für die
KI-Klassifikation (Komponente 3) — Claude versteht alle drei Landessprachen und kann
französische/italienische Artikel-Titel trotz deutscher Themenbeschreibungen in `themes.csv`
korrekt zuordnen, ohne dass du die Themenliste übersetzen musst. Für den Keyword-Vorfilter
(Komponente 2) gilt das nicht automatisch — dort müssten die `example_keywords` um
französische/italienische Begriffe ergänzt werden, sonst filtert der Vorfilter
fremdsprachige Umweltartikel u.U. fälschlich raus, bevor die KI sie überhaupt sieht.

### 5.2 `prefilter.py` (optional, aber empfohlen)

- Nimmt Artikel-Liste + `themes.csv`
- Prüft pro Artikel, ob mindestens eines der `example_keywords` aus irgendeinem Thema im
  Titel/Summary vorkommt (case-insensitive)
- Nur Artikel, die diesen groben Filter bestehen, gehen weiter zu Komponente 3
- **Zweck:** API-Kosten sparen — die meisten Artikel (Sport, Politik, Unterhaltung) haben
  nichts mit Umwelt zu tun und müssen gar nicht erst an die KI geschickt werden

### 5.3 `classify.py` — KI-Klassifikation

**Ein Anthropic-API-Call pro Artikel** (nicht pro Thema — die ganze Themenliste geht als
Kontext mit).

**Prompt-Struktur (sinngemäss):**
```
Du bekommst einen Artikel-Titel und eine kurze Zusammenfassung sowie eine Liste von
Umweltthemen. Ordne den Artikel den passenden Themen zu (0, 1 oder mehrere möglich).
Erfinde keine Themen, die nicht in der Liste stehen. Falls kein Thema passt, gib eine
leere Liste zurück.

Extrahiere zusätzlich, falls im Text erwähnt, einen konkreten Schweizer Ortsnamen
(Gemeinde, Region, Fluss, Berg) als reinen Text — erfinde keinen Ort, falls keiner
erwähnt wird.

Themenliste:
{themes_csv_als_text}

Artikel:
Titel: {title}
Zusammenfassung: {summary}

Antworte NUR als JSON in diesem Format:
{"theme_ids": ["..."], "place_name": "..." oder null, "confidence": "high"|"medium"|"low"}
```

**Wichtige Prinzipien (siehe Projekt-Historie):**
- Die KI klassifiziert nur gegen die vorgegebene Liste, erfindet keine neuen Themen
- Die KI extrahiert Ortsnamen nur als Text, berechnet **niemals selbst Koordinaten** —
  das übernimmt Komponente 4 über eine offizielle Geocoding-Quelle
- JSON-Output strikt parsen, bei Parse-Fehlern Artikel überspringen und loggen, nicht raten

**Batching-Überlegung:** Bei vielen Artikeln pro Tag können mehrere Artikel in einem Call
zusammengefasst werden (Liste von Artikeln rein, Liste von Klassifikationen raus), um API-Calls
zu sparen — für den ersten Durchlauf aber erst 1 Artikel pro Call implementieren (einfacher zu
debuggen), Batching als spätere Optimierung.

### 5.4 `geocode.py` — Bounding Box ermitteln

Nutzt die **GeoAdmin SearchServer API** (offizielle swisstopo-API, kein Key nötig):

```
GET https://api3.geo.admin.ch/rest/services/api/SearchServer
    ?searchText={place_name}
    &type=locations
    &origins=gg25,swissnames
    &sr=2056
```

- `origins=gg25` für Gemeinden/Bezirke, `origins=swissnames` für Flurnamen/Berge/Flüsse —
  beide kombiniert übergeben für breitere Trefferquote
- Aus dem ersten/besten Treffer die Bounding Box extrahieren (Attribut `geom_st_box2d` in der
  Antwort, Format wie `BOX(minE minN, maxE maxN)`, in LV95) — falls dieses Feld fehlt, ersatzweise
  einen festen Puffer (z.B. 2 km) um den zurückgegebenen Punkt (`attrs.y`/`attrs.x`, Achtung:
  swisstopo vertauscht x/y historisch — `y` = Easting, `x` = Northing) legen
- **Die LV95-Box wird nur intern für die Umrechnung gebraucht** — gespeichert wird ausschliesslich
  `bbox_wgs84` (z.B. mit `pyproj`, EPSG:2056 → EPSG:4326 umrechnen). Kein separates LV95-Feld in
  `sightings.csv`
- Kein Treffer gefunden → `bbox_wgs84` leer lassen, nicht raten

### 5.5 `storage.py`

- Lädt bestehende `data/sightings.csv` (falls vorhanden)
- Hängt neue Zeilen an
- Dedupliziert über `(date, theme_id, source, article_url)` — derselbe Artikel wird nicht
  doppelt gespeichert, falls das Skript am selben Tag mehrfach läuft
- Schreibt die Datei zurück

**Git-History als Zeitreihe** (Muster wie im gym-occupancy-Projekt):
- Der GitHub-Actions-Workflow committed nach jedem Lauf die aktualisierte `sightings.csv`
- Da die Datei nur wächst (append-only), ist die volle Historie ohnehin in der aktuellen
  Datei enthalten — anders als bei gym-occupancy (wo die CSV überschrieben und nur die
  Git-History die Zeitreihe ist), reicht hier eine einzige, wachsende CSV-Datei
- Optional später: zusätzliches Skript, das die Git-Commit-Historie ausliest, um z.B.
  "Stand der Datenbank am Tag X" zu rekonstruieren

### 5.6 `.github/workflows/daily_scrape.yml`

```yaml
name: Daily Umwelt-Barometer Scrape

on:
  schedule:
    - cron: "0 5 * * *"   # täglich 05:00 UTC = 07:00 MEZ
  workflow_dispatch: {}     # erlaubt manuellen Start zum Testen

jobs:
  scrape:
    runs-on: ubuntu-latest
    permissions:
      contents: write        # nötig, um Commits zu pushen
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - run: pip install -r requirements.txt

      - name: Run pipeline
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python src/main.py

      - name: Commit results
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/sightings.csv
          git diff --quiet --cached || git commit -m "Daily data: $(date -u +%Y-%m-%d)"
          git push
```

**Secret einrichten:** Im GitHub-Repo unter Settings → Secrets and variables → Actions →
`ANTHROPIC_API_KEY` hinterlegen. Nie den Key im Code oder in `.env` committen.

---

## 6. Implementierungsplan (Reihenfolge für Claude Code)

1. **Repo-Grundgerüst anlegen** — Ordnerstruktur wie oben, `requirements.txt`
   (`feedparser`, `anthropic`, `pandas`, `pyproj`, `requests`, `pyyaml`)
2. **`themes.csv` mit 10-15 Start-Themen befüllen** (Platzhalter, später erweiterbar)
3. **`fetch_rss.py` implementieren und lokal testen** — Feeds aus `feeds.yaml` lesen,
   Artikel-Liste ausgeben, Anzahl pro Feed loggen
4. **`prefilter.py` implementieren** — auf Testartikeln prüfen, dass sinnvoll gefiltert wird
5. **`classify.py` implementieren** — zuerst mit 2-3 Testartikeln von Hand prüfen, ob die
   JSON-Antwort korrekt geparst wird und Themen korrekt zugeordnet werden
6. **`geocode.py` implementieren** — mit bekannten Testorten (z.B. "Bern", "Emmental")
   verifizieren, dass plausible Bounding Boxes zurückkommen
7. **`storage.py` implementieren** — Dedupe-Logik testen (Skript zweimal laufen lassen,
   prüfen dass keine Duplikate entstehen)
8. **`main.py` schreiben**, das alle Komponenten in Reihenfolge orchestriert
9. **Lokal einen kompletten Durchlauf testen** (mit echtem `ANTHROPIC_API_KEY` in `.env`,
   NICHT committen)
10. **GitHub-Actions-Workflow einrichten**, Secret hinterlegen, mit `workflow_dispatch`
    manuell einmal testen
11. **Cron aktivieren**, nach 1-2 Wochen prüfen, ob `sightings.csv` sich sinnvoll füllt

---

## 7. Offene Entscheidungen (vor Umsetzung zu klären)

- **Repo-Sichtbarkeit**: privates oder öffentliches GitHub-Repo? (Artikel-Titel werden ja
  gespeichert — bei einem BAFU-Projekt eher privates Repo sinnvoll)
- **Anzahl Start-Themen**: Vorschlag 10-15, endgültige Liste musst du inhaltlich festlegen
  (idealerweise in Anlehnung an die BAFU-Klima-Risikoanalyse, siehe Abschnitt 4.1)
- **Claude-Modell für Klassifikation**: für diese Aufgabe (Klassifikation + Extraktion)
  reicht ein kleineres/günstigeres Modell wie Claude Haiku — die volle Kapazität eines
  grösseren Modells ist hierfür nicht nötig

---

## 8. Nicht-Ziele von v1 (bewusst ausgeklammert)

- 0-100-Score-Berechnung / Normalisierung zwischen Themen
- Dashboard oder Visualisierung
- Abgleich mit BAFU-Klima-Risikoanalyse (spätere Ausbaustufe)
- Historisches Backfilling — Datensammlung beginnt erst ab dem Tag der ersten Ausführung
- Vollständige Abdeckung aller Landessprachen/Regionen — v1 deckt eine Auswahl ab, kein
  Anspruch auf lückenlose föderale Repräsentativität

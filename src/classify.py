"""Komponente 3: KI-Klassifikation der Artikel in Batches (Groq API)."""

import csv
import json
import logging
import time
from pathlib import Path

from groq import Groq

logger = logging.getLogger(__name__)

DEFAULT_THEMES_PATH = Path(__file__).resolve().parent.parent / "config" / "themes.csv"

# llama-3.3-70b-versatile statt 3.1-8b-instant:
# Das 8b-Modell ist zu schwach für restriktive Mehrfach-Klassifikation gegen 52 Kategorien
# und neigt dazu, bei Batch-Prompts viele Themen pauschal zuzuordnen statt selektiv zu sein.
# 70b ist deutlich besser in der Befolgung komplexer Instruktionen (HAUPTINHALT-Regel).
DEFAULT_MODEL = "llama-3.3-70b-versatile"

VALID_CONFIDENCE = {"high", "medium", "low"}

BATCH_PROMPT_TEMPLATE = """Du bekommst eine Liste von Artikeln (jeweils mit ID, Titel und Zusammenfassung) sowie eine Liste von Umweltthemen.
Ordne jeden Artikel den passenden Themen zu (0, 1 oder mehrere möglich). Erfinde keine Themen, die nicht in der Liste stehen.

WICHTIG: Sei restriktiv bei der Zuordnung. Klassifiziere einen Artikel NUR dann zu einem Thema,
wenn das Thema der HAUPTINHALT oder ein zentraler Aspekt des Artikels ist.
Reine Bauprojekte oder Infrastrukturvorhaben (wie Strassen- oder Bahnausbauten) sollen NICHT
automatisch bei 'klima_allgemein' oder 'wirtschaft_konsum' landen, es sei denn, der Artikel
thematisiert explizit eine ökologische Debatte, CO2-Bilanzen oder konkrete Umweltauflagen.

Berücksichtige nur Artikel, die sich auf die Schweiz beziehen (Ereignis, Ort oder Auswirkung
in der Schweiz). Falls sich ein Artikel auf ein rein ausländisches Ereignis bezieht, gib eine
leere Themenliste zurück.

Falls kein Thema wirklich substanziell passt, gib eine leere Liste zurück.

Beispiel für korrekte Zurückhaltung:
Artikel: "Neues Bahngleis zwischen Zürich und Winterthur eröffnet" (ohne Umweltbezug)
Korrekte Antwort: {{"theme_ids": [], "place_name": "Zürich", "confidence": "high"}}

Extrahiere zusätzlich, falls im Text erwähnt, einen konkreten Schweizer Ortsnamen
(Gemeinde, Region, Fluss, Berg) als reinen Text — erfinde keinen Ort, falls keiner erwähnt wird.

Themenliste:
{themes_csv_als_text}

Artikel-Liste:
{articles_json}

Antworte AUSSCHLIESSLICH als valides JSON-Objekt in diesem Format, ein Eintrag pro Artikel-ID:
{{
  "art_0": {{"theme_ids": ["..."], "place_name": "...", "confidence": "high"}},
  "art_1": {{"theme_ids": [], "place_name": null, "confidence": "high"}}
}}
"""


def load_themes_as_text(themes_path: Path = DEFAULT_THEMES_PATH) -> str:
    lines = []
    with open(themes_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lines.append(f"- {row['theme_id']}: {row['label_de']} — {row['description']}")
    return "\n".join(lines)


def _valid_theme_ids(themes_path: Path = DEFAULT_THEMES_PATH) -> set[str]:
    with open(themes_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return {row["theme_id"] for row in reader}


def _classify_batch(
    client: Groq,
    chunk: list[dict],
    themes_text: str,
    valid_theme_ids: set[str],
    attempt: int = 1,
) -> list[dict]:
    """Klassifiziert einen einzelnen Batch. Bei Parse-Fehler: Divide & Conquer (max. 1x)."""
    prompt_articles = [
        {
            "id": f"art_{i}",
            "title": art.get("title", ""),
            "summary": art.get("summary", ""),
        }
        for i, art in enumerate(chunk)
    ]

    prompt = BATCH_PROMPT_TEMPLATE.format(
        themes_csv_als_text=themes_text,
        articles_json=json.dumps(prompt_articles, ensure_ascii=False, indent=2),
    )

    try:
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        raw_text = response.choices[0].message.content.strip()
        parsed_batch = json.loads(raw_text)

    except json.JSONDecodeError as exc:
        if attempt == 1 and len(chunk) > 1:
            # Divide & Conquer: Batch in zwei Hälften aufteilen und nochmal versuchen
            logger.warning(
                "JSON-Parse-Fehler bei Batch (Grösse %d), teile in zwei Hälften: %s",
                len(chunk), exc,
            )
            mid = len(chunk) // 2
            time.sleep(3)
            left = _classify_batch(client, chunk[:mid], themes_text, valid_theme_ids, attempt=2)
            time.sleep(3)
            right = _classify_batch(client, chunk[mid:], themes_text, valid_theme_ids, attempt=2)
            return left + right
        else:
            logger.error("JSON-Parse-Fehler auch nach Aufteilung, überspringe Batch: %s", exc)
            return [_empty_result(art) for art in chunk]

    except Exception as exc:
        logger.error("API-Fehler bei Batch: %s", exc)
        return [_empty_result(art) for art in chunk]

    # Ergebnisse per ID matchen (nicht per Position)
    results = []
    for i, art in enumerate(chunk):
        res_key = f"art_{i}"
        parsed = parsed_batch.get(res_key, {})

        if not isinstance(parsed, dict):
            logger.warning("Unerwartetes Format für %s, überspringe.", res_key)
            results.append(_empty_result(art))
            continue

        theme_ids = parsed.get("theme_ids", [])
        theme_ids = (
            [t for t in theme_ids if t in valid_theme_ids]
            if isinstance(theme_ids, list)
            else []
        )

        confidence = parsed.get("confidence")
        if confidence not in VALID_CONFIDENCE:
            confidence = None

        results.append({
            "article": art,
            "classification": {
                "theme_ids": theme_ids,
                "place_name": parsed.get("place_name") or None,
                "confidence": confidence,
            },
        })

    return results


def _empty_result(art: dict) -> dict:
    """Leeres Ergebnis für einen Artikel, bei dem die Klassifikation fehlgeschlagen ist."""
    return {
        "article": art,
        "classification": {
            "theme_ids": [],
            "place_name": None,
            "confidence": None,
        },
    }


def classify_articles(
    articles: list[dict],
    themes_path: Path = DEFAULT_THEMES_PATH,
    batch_size: int = 5,
) -> list[dict]:
    """Klassifiziert Artikel in Batches via Groq API.

    Args:
        articles: Liste von Artikel-Dicts mit 'title', 'summary', 'url', 'source'.
        themes_path: Pfad zur themes.csv.
        batch_size: Artikel pro Batch (Standard 5 — bei 70b-Modell und 52 Themen
                    ca. 2'500 Tokens/Batch, gut innerhalb des 6'000-Token/min-Limits).

    Returns:
        Liste von Dicts mit 'article' und 'classification' (theme_ids, place_name, confidence).
    """
    client = Groq()
    themes_text = load_themes_as_text(themes_path)
    valid_theme_ids = _valid_theme_ids(themes_path)

    results = []

    for chunk_start in range(0, len(articles), batch_size):
        if chunk_start > 0:
            # Pause zwischen Batches wegen 6'000-Tokens/min-Limit bei Groq Gratis-Tarif
            time.sleep(5)

        chunk = articles[chunk_start: chunk_start + batch_size]
        logger.info(
            "Klassifiziere Batch %d-%d von %d Artikeln",
            chunk_start + 1, min(chunk_start + batch_size, len(articles)), len(articles),
        )

        batch_results = _classify_batch(client, chunk, themes_text, valid_theme_ids)
        results.extend(batch_results)

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_articles = [
        {
            "title": "Hitzestress im Wald – Sonnenbrand bei Bäumen",
            "summary": "Der Klimawandel trifft die Region Basel hart. Immer häufiger braucht es Notfällungen.",
            "url": "https://example.com/1",
            "source": "srf",
        },
        {
            "title": "Wenn der Redefluss stockt – Im Stottercamp reden lernen",
            "summary": "Kinder lernen im Ferienlager den Umgang mit ihrer Sprachstörung.",
            "url": "https://example.com/2",
            "source": "srf",
        },
        {
            "title": "Neue Bahnstrecke Zürich–Winterthur eröffnet",
            "summary": "Der Ausbau beseitigt einen Engpass im nationalen Bahnverkehr.",
            "url": "https://example.com/3",
            "source": "srf",
        },
    ]
    for r in classify_articles(test_articles):
        print(r["article"]["title"])
        print(" ->", r["classification"])
        print()

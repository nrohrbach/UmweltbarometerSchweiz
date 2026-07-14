"""Komponente 3: KI-Klassifikation der Artikel gegen die Themenliste (Groq API, Batch-Verarbeitung)."""

import csv
import json
import logging
import os
import time
from pathlib import Path

from groq import Groq

logger = logging.getLogger(__name__)

DEFAULT_THEMES_PATH = Path(__file__).resolve().parent.parent / "config" / "themes.csv"
DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
BATCH_SIZE = int(os.environ.get("GROQ_BATCH_SIZE", "12"))
BATCH_PAUSE_SECONDS = 5

VALID_CONFIDENCE = {"high", "medium", "low"}

PROMPT_TEMPLATE = """Du bekommst eine Liste von Artikeln (Titel + Zusammenfassung, je mit einer ID) sowie eine
Liste von Umweltthemen. Ordne JEDEN Artikel einzeln den passenden Themen zu (0, 1 oder
mehrere möglich). Erfinde keine Themen, die nicht in der Liste stehen. Falls bei einem
Artikel kein Thema passt, gib für diesen Artikel eine leere Themenliste zurück.

Wichtig: Nur Artikel berücksichtigen, die sich auf die Schweiz beziehen (Ereignis, Ort oder
Auswirkung in der Schweiz). Falls sich ein Artikel auf ein rein ausländisches Ereignis
bezieht (z.B. Naturkatastrophe im Ausland, auch wenn Schweizer Personen/Organisationen
involviert sind), gib für diesen Artikel eine leere Themenliste zurück — auch wenn das
Thema inhaltlich passen würde.

Extrahiere zusätzlich pro Artikel, falls im Text erwähnt, einen konkreten Schweizer
Ortsnamen (Gemeinde, Region, Fluss, Berg) als reinen Text — erfinde keinen Ort, falls
keiner erwähnt wird.

Themenliste:
{themes_csv_als_text}

Artikel:
{articles_json}

Antworte NUR als JSON-Array in diesem Format, ein Eintrag pro Artikel-ID, in derselben
Reihenfolge wie die Eingabe:
[
  {{"id": "a1", "theme_ids": ["..."], "place_name": "..." oder null, "confidence": "high"|"medium"|"low"}},
  {{"id": "a2", "theme_ids": [], "place_name": null, "confidence": "low"}},
  ...
]
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


def _extract_json_array(raw_text: str) -> list | None:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[len("json"):]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, list):
        return None
    return parsed


def _call_groq(batch: list[dict], client: Groq, themes_text: str, model: str) -> list | None:
    """Ein API-Call für einen Batch. Gibt das geparste JSON-Array zurück oder None bei Fehler."""
    articles_payload = [
        {"id": f"a{i}", "title": a.get("title", ""), "summary": a.get("summary", "")}
        for i, a in enumerate(batch)
    ]
    prompt = PROMPT_TEMPLATE.format(
        themes_csv_als_text=themes_text,
        articles_json=json.dumps(articles_payload, ensure_ascii=False, indent=2),
    )

    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.choices[0].message.content
    except Exception as exc:
        logger.error("Groq API-Fehler für Batch (%d Artikel): %s", len(batch), exc)
        return None

    parsed = _extract_json_array(raw_text)
    if parsed is None:
        logger.error("JSON-Parse-Fehler für Batch (%d Artikel): %r", len(batch), raw_text)
    return parsed


def classify_batch(
    batch: list[dict],
    client: Groq,
    themes_text: str,
    valid_theme_ids: set[str],
    model: str = DEFAULT_MODEL,
) -> list[dict]:
    """Klassifiziert einen Batch. Bei Parse-Fehlern wird der Batch geteilt und erneut versucht
    (Divide and Conquer), statt den ganzen Lauf abzubrechen."""
    if not batch:
        return []

    parsed = _call_groq(batch, client, themes_text, model)

    if parsed is None:
        if len(batch) == 1:
            logger.error("Klassifikation für '%s' endgültig fehlgeschlagen, wird übersprungen", batch[0].get("title"))
            return []
        mid = len(batch) // 2
        return classify_batch(batch[:mid], client, themes_text, valid_theme_ids, model) + classify_batch(
            batch[mid:], client, themes_text, valid_theme_ids, model
        )

    by_id = {item.get("id"): item for item in parsed if isinstance(item, dict)}

    results = []
    for i, article in enumerate(batch):
        article_id = f"a{i}"
        item = by_id.get(article_id)
        if item is None:
            logger.error("Antwort enthält keine ID '%s' für Artikel '%s', wird übersprungen", article_id, article.get("title"))
            continue

        theme_ids = item.get("theme_ids", [])
        if not isinstance(theme_ids, list):
            logger.error("Ungültiges theme_ids-Format für '%s': %r", article.get("title"), theme_ids)
            continue
        # Nur gegen die vorgegebene Liste klassifizieren — erfundene Themen werden verworfen.
        theme_ids = [t for t in theme_ids if t in valid_theme_ids]

        confidence = item.get("confidence")
        if confidence not in VALID_CONFIDENCE:
            confidence = None

        results.append(
            {
                "article": article,
                "classification": {
                    "theme_ids": theme_ids,
                    "place_name": item.get("place_name") or None,
                    "confidence": confidence,
                },
            }
        )

    return results


def classify_articles(articles: list[dict], themes_path: Path = DEFAULT_THEMES_PATH) -> list[dict]:
    """Klassifiziert alle Artikel in Batches von BATCH_SIZE und gibt eine Liste von
    (article, classification)-Ergebnissen zurück."""
    client = Groq()
    themes_text = load_themes_as_text(themes_path)
    valid_theme_ids = _valid_theme_ids(themes_path)

    results = []
    for start in range(0, len(articles), BATCH_SIZE):
        batch = articles[start : start + BATCH_SIZE]
        results.extend(classify_batch(batch, client, themes_text, valid_theme_ids))
        if start + BATCH_SIZE < len(articles):
            time.sleep(BATCH_PAUSE_SECONDS)

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_articles = [
        {
            "title": "Hitzewelle sorgt für Rekordtemperaturen im Mittelland",
            "summary": "Meteorologen warnen vor gesundheitlichen Risiken durch die anhaltende Hitze.",
            "url": "https://example.com/hitze",
            "source": "test",
        },
        {
            "title": "Waldbrand-Flucht Südfrankreich",
            "summary": "Tausende Touristen, auch Schweizer, mussten evakuiert werden.",
            "url": "https://example.com/waldbrand-frankreich",
            "source": "test",
        },
    ]
    for r in classify_articles(test_articles):
        print(r)

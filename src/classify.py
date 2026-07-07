"""Komponente 3: KI-Klassifikation der Artikel gegen die Themenliste (Groq API)."""

import csv
import json
import logging
import os
from pathlib import Path

from groq import Groq

logger = logging.getLogger(__name__)

DEFAULT_THEMES_PATH = Path(__file__).resolve().parent.parent / "config" / "themes.csv"
DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")

VALID_CONFIDENCE = {"high", "medium", "low"}

PROMPT_TEMPLATE = """Du bekommst einen Artikel-Titel und eine kurze Zusammenfassung sowie eine Liste von
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
{{"theme_ids": ["..."], "place_name": "..." oder null, "confidence": "high"|"medium"|"low"}}
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


def classify_article(
    article: dict,
    client: Groq,
    themes_text: str,
    valid_theme_ids: set[str],
    model: str = DEFAULT_MODEL,
) -> dict | None:
    """Ein API-Call pro Artikel. Gibt None zurück, wenn Parsing fehlschlägt oder ein Fehler auftritt."""
    prompt = PROMPT_TEMPLATE.format(
        themes_csv_als_text=themes_text,
        title=article.get("title", ""),
        summary=article.get("summary", ""),
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            # Erzwingt die Ausgabe eines validen JSON-Objekts durch Groq
            response_format={"type": "json_object"},
            temperature=0.1,  # Niedrige Temperatur für deterministischere Ergebnisse
        )
        raw_text = response.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("Groq API-Fehler für '%s': %s", article.get("title"), exc)
        return None

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.error("JSON-Parse-Fehler für '%s': %r", article.get("title"), raw_text)
        return None

    theme_ids = parsed.get("theme_ids", [])
    if not isinstance(theme_ids, list):
        logger.error("Ungültiges theme_ids-Format für '%s': %r", article.get("title"), theme_ids)
        return None

    # Nur gegen die vorgegebene Liste klassifizieren — erfundene Themen werden verworfen.
    theme_ids = [t for t in theme_ids if t in valid_theme_ids]

    confidence = parsed.get("confidence")
    if confidence not in VALID_CONFIDENCE:
        confidence = None

    return {
        "theme_ids": theme_ids,
        "place_name": parsed.get("place_name") or None,
        "confidence": confidence,
    }


def classify_articles(articles: list[dict], themes_path: Path = DEFAULT_THEMES_PATH) -> list[dict]:
    """Klassifiziert alle Artikel und gibt eine Liste von (article, classification)-Ergebnissen zurück.

    Artikel, bei denen die Klassifikation fehlschlägt, werden übersprungen.
    """
    # Liest automatisch os.environ.get("GROQ_API_KEY")
    client = Groq()
    themes_text = load_themes_as_text(themes_path)
    valid_theme_ids = _valid_theme_ids(themes_path)

    results = []
    for article in articles:
        classification = classify_article(article, client, themes_text, valid_theme_ids)
        if classification is None:
            continue
        results.append({"article": article, "classification": classification})

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_articles = [
        {
            "title": "Hitzewelle sorgt für Rekordtemperaturen im Mittelland",
            "summary": "Meteorologen warnen vor gesundheitlichen Risiken durch die anhaltende Hitze.",
            "url": "https://example.com/hitze",
            "source": "test",
        }
    ]
    for r in classify_articles(test_articles):
        print(r)

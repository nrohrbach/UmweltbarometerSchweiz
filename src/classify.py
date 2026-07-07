"""Komponente 3: KI-Klassifikation der Artikel in Batches (Groq API)."""

import csv
import json
import logging
import time
from pathlib import Path

from groq import Groq

logger = logging.getLogger(__name__)

DEFAULT_THEMES_PATH = Path(__file__).resolve().parent.parent / "config" / "themes.csv"
# 'llama-3.1-8b-instant' ist ideal für Batch-Klassifizierungen bei Groq
DEFAULT_MODEL = "llama-3.1-8b-instant"

VALID_CONFIDENCE = {"high", "medium", "low"}

BATCH_PROMPT_TEMPLATE = """Du bekommst eine Liste von Artikeln (jeweils mit ID, Titel und Zusammenfassung) sowie eine Liste von Umweltthemen.
Ordne jeden Artikel den passenden Themen zu (0, 1 oder mehrere möglich). Erfinde keine Themen, die nicht in der Liste stehen. Falls kein Thema passt, gib eine leere Liste zurück.
WICHTIG: Sei restriktiv bei der Zuordnung. Klassifiziere einen Artikel NUR dann zu einem Thema, 
wenn das Thema der HAUPTINHALT oder ein zentraler Aspekt des Artikels ist. 
Reine Bauprojekte oder Infrastrukturvorhaben (wie Strassen- oder Bahnausbauten) sollen NICHT 
automatisch bei 'klima_allgemein' oder 'wirtschaft_konsum' landen, es sei denn, der Artikel 
thematisiert explizit eine ökologische Debatte, CO2-Bilanzen oder konkrete Umweltauflagen.

Falls kein Thema wirklich substanziell passt, gib eine leere Liste zurück.

Extrahiere zusätzlich, falls im Text erwähnt, einen konkreten Schweizer Ortsnamen (Gemeinde, Region, Fluss, Berg) als reinen Text — erfinde keinen Ort, falls keiner erwähnt wird.

Themenliste:
{themes_csv_als_text}

Artikel-Liste:
{articles_json}

Antworte AUSSCHLIESSLICH als valides JSON-Objekt in diesem Format:
{{
  "art_0": {{"theme_ids": ["..."], "place_name": "...", "confidence": "high"}},
  "art_1": {{"theme_ids": ["..."], "place_name": null, "confidence": "medium"}}
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

def classify_articles(articles: list[dict], themes_path: Path = DEFAULT_THEMES_PATH) -> list[dict]:
    """Klassifiziert Artikel in 5er-Batches, um Tokens zu sparen und API-Limits zu umgehen."""
    client = Groq()
    themes_text = load_themes_as_text(themes_path)
    valid_theme_ids = _valid_theme_ids(themes_path)
    
    results = []
    batch_size = 5
    
    for chunk_start in range(0, len(articles), batch_size):
        # Sicherheits-Pause zwischen den Batches
        if chunk_start > 0:
            time.sleep(2.2)
            
        chunk = articles[chunk_start : chunk_start + batch_size]
        prompt_articles = [
            {"id": f"art_{i}", "title": art.get("title", ""), "summary": art.get("summary", "")}
            for i, art in enumerate(chunk)
        ]
            
        prompt = BATCH_PROMPT_TEMPLATE.format(
            themes_csv_als_text=themes_text,
            articles_json=json.dumps(prompt_articles, ensure_ascii=False, indent=2)
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
            
            for i, art in enumerate(chunk):
                res_key = f"art_{i}"
                parsed = parsed_batch.get(res_key, {})
                
                theme_ids = parsed.get("theme_ids", [])
                theme_ids = [t for t in theme_ids if t in valid_theme_ids] if isinstance(theme_ids, list) else []
                
                confidence = parsed.get("confidence")
                if confidence not in VALID_CONFIDENCE:
                    confidence = None
                    
                results.append({
                    "article": art, 
                    "classification": {
                        "theme_ids": theme_ids,
                        "place_name": parsed.get("place_name") or None,
                        "confidence": confidence
                    }
                })
                
        except Exception as exc:
            logger.error("Fehler bei Batch ab Index %d: %s", chunk_start, exc)
            continue

    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_articles = [
        {"title": "Hitzewelle im Mittelland", "summary": "Meteorologen warnen vor Hitze.", "url": "...", "source": "test"}
    ]
    for r in classify_articles(test_articles):
        print(r)

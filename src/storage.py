"""Komponente 5: Ergebnisse an data/sightings.csv anhängen, dedupliziert."""

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_SIGHTINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "sightings.csv"

COLUMNS = [
    "date",
    "theme_id",
    "source",
    "article_title",
    "article_url",
    "place_name_raw",
    "bbox_wgs84",
    "classification_confidence",
]

DEDUPE_KEYS = ["date", "theme_id", "source", "article_url"]


def load_sightings(path: Path = DEFAULT_SIGHTINGS_PATH) -> pd.DataFrame:
    if path.exists():
        return pd.read_csv(path, dtype=str).fillna("")
    return pd.DataFrame(columns=COLUMNS)


def append_sightings(new_rows: list[dict], path: Path = DEFAULT_SIGHTINGS_PATH) -> pd.DataFrame:
    """Hängt neue Zeilen an, dedupliziert über (date, theme_id, source, article_url)."""
    existing = load_sightings(path)
    new_df = pd.DataFrame(new_rows, columns=COLUMNS).fillna("")

    combined = pd.concat([existing, new_df], ignore_index=True)
    before = len(combined)
    combined = combined.drop_duplicates(subset=DEDUPE_KEYS, keep="first")
    logger.info("Storage: %d neue Zeilen, %d Duplikate verworfen", len(new_df), before - len(combined))

    path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(path, index=False)
    return combined


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_row = {
        "date": "2026-01-01",
        "theme_id": "luft",
        "source": "test",
        "article_title": "Test Artikel",
        "article_url": "https://example.com/test",
        "place_name_raw": "",
        "bbox_wgs84": "",
        "classification_confidence": "high",
    }
    append_sightings([test_row])

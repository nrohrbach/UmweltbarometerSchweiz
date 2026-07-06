"""Komponente 2: Grober Keyword-Vorfilter, spart API-Kosten in Komponente 3."""

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_THEMES_PATH = Path(__file__).resolve().parent.parent / "config" / "themes.csv"


def load_keywords(themes_path: Path = DEFAULT_THEMES_PATH) -> list[str]:
    keywords = []
    with open(themes_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = row.get("example_keywords", "")
            if not raw:
                continue
            keywords.extend(kw.strip().lower() for kw in raw.split(",") if kw.strip())
    return keywords


def filter_articles(articles: list[dict], themes_path: Path = DEFAULT_THEMES_PATH) -> list[dict]:
    """Behält nur Artikel, die mindestens ein Themen-Stichwort in Titel/Summary enthalten."""
    keywords = load_keywords(themes_path)
    matched = []

    for article in articles:
        haystack = f"{article.get('title', '')} {article.get('summary', '')}".lower()
        if any(kw in haystack for kw in keywords):
            matched.append(article)

    logger.info("Vorfilter: %d von %d Artikeln bestehen", len(matched), len(articles))
    return matched


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from fetch_rss import fetch_articles

    all_articles = fetch_articles()
    result = filter_articles(all_articles)
    print(f"{len(result)} von {len(all_articles)} Artikeln bestehen den Vorfilter")

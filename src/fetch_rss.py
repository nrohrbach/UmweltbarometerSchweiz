"""Komponente 1: RSS-Feeds abfragen und Artikel-Liste liefern."""

import logging
from pathlib import Path

import feedparser
import yaml

logger = logging.getLogger(__name__)

DEFAULT_FEEDS_PATH = Path(__file__).resolve().parent.parent / "config" / "feeds.yaml"


def load_feeds(feeds_path: Path = DEFAULT_FEEDS_PATH) -> list[dict]:
    with open(feeds_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("feeds", [])


def fetch_articles(feeds_path: Path = DEFAULT_FEEDS_PATH) -> list[dict]:
    """Liest alle konfigurierten Feeds und gibt eine flache Artikel-Liste zurück.

    Ein einzelner kaputter Feed darf den gesamten Lauf nicht abbrechen.
    """
    feeds = load_feeds(feeds_path)
    articles = []

    for feed in feeds:
        name = feed["name"]
        url = feed["url"]
        try:
            parsed = feedparser.parse(url)
            if parsed.bozo and not parsed.entries:
                raise parsed.bozo_exception or RuntimeError("Feed enthält keine Einträge")

            count = 0
            for entry in parsed.entries:
                articles.append(
                    {
                        "title": entry.get("title", "").strip(),
                        "summary": entry.get("summary", "").strip(),
                        "url": entry.get("link", "").strip(),
                        "source": name,
                        "published": entry.get("published", ""),
                    }
                )
                count += 1
            logger.info("Feed %s: %d Artikel geladen", name, count)
        except Exception as exc:
            logger.error("Feed %s konnte nicht geladen werden: %s", name, exc)
            continue

    return articles


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = fetch_articles()
    print(f"Total: {len(result)} Artikel")

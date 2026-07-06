"""Orchestriert den täglichen Lauf: fetch -> prefilter -> classify -> geocode -> storage."""

import logging
from datetime import datetime, timezone

from classify import classify_articles
from fetch_rss import fetch_articles
from geocode import geocode_place
from prefilter import filter_articles
from storage import append_sightings

logger = logging.getLogger(__name__)


def run() -> None:
    today = datetime.now(timezone.utc).date().isoformat()

    articles = fetch_articles()
    logger.info("%d Artikel aus RSS-Feeds geladen", len(articles))

    candidates = filter_articles(articles)
    logger.info("%d Artikel bestehen den Keyword-Vorfilter", len(candidates))

    classified = classify_articles(candidates)
    logger.info("%d Artikel erfolgreich klassifiziert", len(classified))

    rows = []
    place_bbox_cache: dict[str, str | None] = {}

    for entry in classified:
        article = entry["article"]
        classification = entry["classification"]
        theme_ids = classification["theme_ids"]
        if not theme_ids:
            continue

        place_name = classification["place_name"]
        bbox = None
        if place_name:
            if place_name not in place_bbox_cache:
                place_bbox_cache[place_name] = geocode_place(place_name)
            bbox = place_bbox_cache[place_name]

        for theme_id in theme_ids:
            rows.append(
                {
                    "date": today,
                    "theme_id": theme_id,
                    "source": article["source"],
                    "article_title": article["title"],
                    "article_url": article["url"],
                    "place_name_raw": place_name or "",
                    "bbox_wgs84": bbox or "",
                    "classification_confidence": classification["confidence"] or "",
                }
            )

    logger.info("%d neue Sichtungen (Artikel x Thema)", len(rows))
    append_sightings(rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()

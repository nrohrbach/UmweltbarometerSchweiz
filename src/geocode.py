"""Komponente 4: Ortsname -> Bounding Box via GeoAdmin SearchServer (swisstopo)."""

import logging
import re

import requests
from pyproj import Transformer

logger = logging.getLogger(__name__)

SEARCHSERVER_URL = "https://api3.geo.admin.ch/rest/services/api/SearchServer"
FALLBACK_BUFFER_M = 2000  # Meter, falls keine Bounding Box im Treffer enthalten ist

# LV95 (EPSG:2056) -> WGS84 (EPSG:4326)
_transformer = Transformer.from_crs("EPSG:2056", "EPSG:4326", always_xy=True)

_BOX_RE = re.compile(
    r"BOX\(\s*([\-\d.]+)\s+([\-\d.]+)\s*,\s*([\-\d.]+)\s+([\-\d.]+)\s*\)"
)


def _lv95_to_wgs84_bbox(min_e: float, min_n: float, max_e: float, max_n: float) -> str:
    min_lon, min_lat = _transformer.transform(min_e, min_n)
    max_lon, max_lat = _transformer.transform(max_e, max_n)
    return f"{min_lon:.6f},{min_lat:.6f},{max_lon:.6f},{max_lat:.6f}"


def geocode_place(place_name: str) -> str | None:
    """Sucht einen Ortsnamen via GeoAdmin SearchServer und gibt die Bounding Box in WGS84 zurück.

    Format: "minLon,minLat,maxLon,maxLat". Gibt None zurück, falls kein Treffer gefunden wird.
    """
    if not place_name:
        return None

    try:
        response = requests.get(
            SEARCHSERVER_URL,
            params={
                "searchText": place_name,
                "type": "locations",
                "origins": "gg25,swissnames",
                "sr": 2056,
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.error("Geocoding-Fehler für '%s': %s", place_name, exc)
        return None

    results = data.get("results", [])
    if not results:
        logger.info("Kein Geocoding-Treffer für '%s'", place_name)
        return None

    best = results[0]
    attrs = best.get("attrs", {})

    box_str = attrs.get("geom_st_box2d")
    if box_str:
        match = _BOX_RE.match(box_str)
        if match:
            min_e, min_n, max_e, max_n = (float(v) for v in match.groups())
            return _lv95_to_wgs84_bbox(min_e, min_n, max_e, max_n)

    # Fallback: fester Puffer um den Punkt. Achtung: swisstopo vertauscht x/y historisch
    # (y = Easting, x = Northing).
    easting = attrs.get("y")
    northing = attrs.get("x")
    if easting is None or northing is None:
        logger.info("Kein bbox/Punkt-Attribut für '%s'", place_name)
        return None

    return _lv95_to_wgs84_bbox(
        easting - FALLBACK_BUFFER_M,
        northing - FALLBACK_BUFFER_M,
        easting + FALLBACK_BUFFER_M,
        northing + FALLBACK_BUFFER_M,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for test_place in ["Bern", "Emmental", "Nichtexistierenderort123"]:
        print(test_place, "->", geocode_place(test_place))

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).resolve().parent / "data" / "station_arrival_catalog.json"


class ArrivalStationCatalogItem(TypedDict):
    subway_id: str
    station_id: str
    name: str
    line: str
    line_color: str
    latitude: float | None
    longitude: float | None


@lru_cache(maxsize=1)
def load_arrival_station_catalog() -> tuple[ArrivalStationCatalogItem, ...]:
    if not CATALOG_PATH.exists():
        logger.debug(
            "station catalog file not found at %s",
            CATALOG_PATH,
        )
        raise FileNotFoundError(f"Station catalog file not found: {CATALOG_PATH}")

    raw = CATALOG_PATH.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, list):
        logger.debug(
            "station catalog root is %s, expected list; file: %s",
            type(payload).__name__,
            CATALOG_PATH,
        )
        raise ValueError(
            f"Station catalog must be a JSON array, got {type(payload).__name__}"
        )

    items: list[ArrivalStationCatalogItem] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        subway_id = str(row.get("subway_id") or "").strip()
        station_id = str(row.get("station_id") or "").strip()
        name = str(row.get("name") or "").strip()
        line = str(row.get("line") or "").strip()
        line_color = str(row.get("line_color") or "").strip()
        if not subway_id or not station_id or not name or not line:
            continue
        raw_lat = row.get("latitude")
        raw_lng = row.get("longitude")
        items.append(
            {
                "subway_id": subway_id,
                "station_id": station_id,
                "name": name,
                "line": line,
                "line_color": line_color or "#7CA8D5",
                "latitude": float(raw_lat) if raw_lat is not None else None,
                "longitude": float(raw_lng) if raw_lng is not None else None,
            }
        )

    logger.debug("loaded %d stations from catalog %s", len(items), CATALOG_PATH)
    return tuple(items)


def normalize_station_name(value: str) -> str:
    return value.strip().replace(" ", "").removesuffix("역")


def normalize_line_name(value: str) -> str:
    line = value.strip()
    if line == "서울4호선":
        return "4호선"
    return line

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TypedDict


CATALOG_PATH = Path(__file__).resolve().parent / "data" / "station_arrival_catalog.json"


class ArrivalStationCatalogItem(TypedDict):
    subway_id: str
    station_id: str
    name: str
    line: str
    line_color: str


@lru_cache(maxsize=1)
def load_arrival_station_catalog() -> tuple[ArrivalStationCatalogItem, ...]:
    if not CATALOG_PATH.exists():
        return ()

    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return ()

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
        items.append(
            {
                "subway_id": subway_id,
                "station_id": station_id,
                "name": name,
                "line": line,
                "line_color": line_color or "#7CA8D5",
            }
        )

    return tuple(items)


def normalize_station_name(value: str) -> str:
    return value.strip().replace(" ", "").removesuffix("역")


def normalize_line_name(value: str) -> str:
    line = value.strip()
    if line == "서울4호선":
        return "4호선"
    return line

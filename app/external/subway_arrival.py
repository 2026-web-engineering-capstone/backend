from __future__ import annotations

import re

import httpx

from app.schemas import ArrivalCardResponse, TrainEntryResponse

_ARRIVAL_API_BASE = "http://swopenapi.seoul.go.kr/api/subway"


async def fetch_arrival_cards(station_name: str, api_key: str, line_color: str) -> list[ArrivalCardResponse]:
    if not api_key:
        return []

    # station_name includes "역" suffix; API expects name without it
    name = station_name.removesuffix("역")
    url = f"{_ARRIVAL_API_BASE}/{api_key}/json/realtimeStationArrival/0/10/{name}"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            body = response.json()
    except Exception:
        return []

    error = body.get("errorMessage", {})
    if error.get("code") not in ("INFO-000", "INFO-200"):
        return []

    arrivals = body.get("realtimeArrivalList", [])
    return _build_cards(arrivals, line_color)


def _clean_direction(raw: str) -> str:
    cleaned = re.sub(r'\s*\([^)]+\)', '', raw)  # strip (막차), (첫차) etc.
    return cleaned.removesuffix('방면').strip()


def _build_cards(arrivals: list[dict], line_color: str) -> list[ArrivalCardResponse]:
    # trainLineNm format: "{finalDest}행 - {directionStation}방면"
    # Group by directionStation so same-direction trains merge into one card.
    by_direction: dict[str, list[tuple[int, str]]] = {}

    for item in arrivals:
        raw = item.get("trainLineNm", "")
        parts = raw.split(" - ", 1)
        if len(parts) == 2:
            final_dest = parts[0].removesuffix("행").strip()
            direction = _clean_direction(parts[1])
        else:
            direction = _clean_direction(raw.removesuffix("행"))
            final_dest = direction

        if not direction:
            continue
        try:
            seconds = int(item.get("barvlDt", 0))
        except (ValueError, TypeError):
            continue
        minutes = seconds // 60
        by_direction.setdefault(direction, []).append((minutes, final_dest))

    cards: list[ArrivalCardResponse] = []
    for direction, trains in by_direction.items():
        trains.sort(key=lambda t: t[0])
        first = TrainEntryResponse(minutes=trains[0][0], destination=f"{trains[0][1]}행") if trains else None
        second = TrainEntryResponse(minutes=trains[1][0], destination=f"{trains[1][1]}행") if len(trains) >= 2 else None
        cards.append(ArrivalCardResponse(
            display_direction=f"{direction} 방면",
            line_color=line_color,
            first_train=first,
            second_train=second,
        ))

    return cards

import time

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_settings
from app.external.accessibility import fetch_facilities
from app.external.subway_arrival import fetch_arrival_cards
from app.schemas import ApiResponse

router = APIRouter(prefix="/transit", tags=["transit"])


@router.get("/arrivals")
async def get_arrivals(
    station_name: str = Query(..., min_length=1),
    settings=Depends(get_settings),
):
    cards = await fetch_arrival_cards(
        station_name, settings.subway_arrival_api_key, ""
    )
    trains = []
    for card in cards:
        for entry in [card.first_train, card.second_train]:
            if entry is not None:
                trains.append(
                    {
                        "trainNumber": None,
                        "destination": entry.destination,
                        "etaMessage": "곧 도착" if entry.minutes == 0 else f"{entry.minutes}분",
                        "direction": card.display_direction,
                        "line": None,
                    }
                )
    return ApiResponse(
        data={
            "stationName": station_name,
            "fetchedAt": int(time.time() * 1000),
            "trains": trains,
        }
    )


@router.get("/facilities")
async def get_facilities(
    station_name: str = Query(..., min_length=1),
    settings=Depends(get_settings),
):
    summary = await fetch_facilities(station_name, settings.accessibility_api_key)

    def status(has: bool) -> str:
        return "operational" if has else "unknown"

    facilities = [
        {
            "facilityType": "elevator",
            "locationNote": None,
            "operationalStatus": status(summary.elevator),
        },
        {
            "facilityType": "accessible_toilet",
            "locationNote": None,
            "operationalStatus": status(summary.accessible_toilet),
        },
        {
            "facilityType": "wheelchair_lift",
            "locationNote": None,
            "operationalStatus": status(summary.wheelchair_lift),
        },
        {
            "facilityType": "escalator",
            "locationNote": None,
            "operationalStatus": status(summary.escalator),
        },
    ]
    return ApiResponse(
        data={
            "stationName": station_name,
            "fetchedAt": int(time.time() * 1000),
            "facilities": facilities,
        }
    )

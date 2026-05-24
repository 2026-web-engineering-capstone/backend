from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_service, get_settings
from app.external.accessibility import fetch_facilities
from app.external.subway_arrival import fetch_arrival_cards
from app.schemas import (
    ApiResponse,
    CurrentStationResponse,
    NeighborStationResponse,
    StationContextResponse,
    StationResponse,
    StationSummaryResponse,
)
from app.service import AppService

router = APIRouter(prefix="/stations", tags=["stations"])


@router.get("", response_model=ApiResponse)
def list_stations(
    query: str | None = Query(default=None),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
):
    stations = service.list_stations(db, query)
    return ApiResponse(data=[StationResponse.model_validate(item) for item in stations])


@router.get("/{station_id}/summary", response_model=ApiResponse)
async def get_station_summary(
    station_id: str,
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
    settings=Depends(get_settings),
):
    station = service.get_station_by_id(db, station_id)
    prev, nxt = service.get_station_neighbors(db, station_id)

    arrival_cards, facilities = await _fetch_external(station.name, station.line_color, settings)

    context = StationContextResponse(
        previous=NeighborStationResponse(id=prev.id, name=prev.name.removesuffix("역")) if prev else None,
        current=CurrentStationResponse(
            id=station.id,
            name=station.name,
            name_short=station.name.removesuffix("역"),
            latitude=station.latitude,
            longitude=station.longitude,
            line=station.line,
            line_label=station.line_label,
            line_color=station.line_color,
            line_color_soft=station.line_color_soft,
        ),
        next=NeighborStationResponse(id=nxt.id, name=nxt.name.removesuffix("역")) if nxt else None,
    )

    return ApiResponse(data=StationSummaryResponse(
        station_context=context,
        arrival_cards=arrival_cards,
        facilities=facilities,
    ))


async def _fetch_external(station_name: str, line_color: str, settings):
    import asyncio
    arrivals_task = fetch_arrival_cards(station_name, settings.subway_arrival_api_key, line_color)
    facilities_task = fetch_facilities(station_name, settings.accessibility_api_key)
    return await asyncio.gather(arrivals_task, facilities_task)

"""실시간 도착 / 역사 편의시설 외부 API 프록시 라우터."""

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings
from app.dependencies import get_current_user, get_settings
from app.models import User
from app.schemas import ApiResponse
from app.services.transit_service import (
    ArrivalTrain,
    StationArrivals,
    StationFacilities,
    StationFacility,
    fetch_station_arrivals,
    fetch_station_facilities,
)

router = APIRouter(prefix="/transit", tags=["transit"])


def _serialize_arrivals(payload: StationArrivals) -> dict:
    return {
        "stationName": payload.station_name,
        "fetchedAt": payload.fetched_at,
        "trains": [_serialize_train(train) for train in payload.trains],
    }


def _serialize_train(train: ArrivalTrain) -> dict:
    return {
        "trainNumber": train.train_number,
        "destination": train.destination,
        "etaMessage": train.eta_message,
        "direction": train.direction,
        "line": train.line,
    }


def _serialize_facilities(payload: StationFacilities) -> dict:
    return {
        "stationName": payload.station_name,
        "fetchedAt": payload.fetched_at,
        "facilities": [_serialize_facility(item) for item in payload.facilities],
    }


def _serialize_facility(item: StationFacility) -> dict:
    return {
        "facilityType": item.facility_type,
        "locationNote": item.location_note,
        "operationalStatus": item.operational_status,
    }


@router.get("/arrivals", response_model=ApiResponse)
async def get_station_arrivals(
    station_name: str,
    _: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    if not station_name.strip():
        raise HTTPException(status_code=422, detail="station_name is required")
    payload = await fetch_station_arrivals(settings, station_name)
    return ApiResponse(data=_serialize_arrivals(payload))


@router.get("/facilities", response_model=ApiResponse)
async def get_station_facilities(
    station_name: str,
    _: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    if not station_name.strip():
        raise HTTPException(status_code=422, detail="station_name is required")
    payload = await fetch_station_facilities(settings, station_name)
    return ApiResponse(data=_serialize_facilities(payload))

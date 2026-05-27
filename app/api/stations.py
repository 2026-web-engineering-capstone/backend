import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_service
from app.schemas import ApiResponse, NearestStationResponse, StationResponse
from app.service import AppService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stations", tags=["stations"])


@router.get("/nearest", response_model=ApiResponse)
def nearest_stations(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    limit: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
):
    try:
        results = service.find_nearest_stations(db, lat, lng, limit)
    except Exception:
        logger.exception("nearest_stations 처리 중 오류 발생 (lat=%s, lng=%s)", lat, lng)
        raise
    return ApiResponse(data=[
        NearestStationResponse(
            id=station.id,
            name=station.name,
            line=station.line,
            line_color=station.line_color,
            latitude=station.latitude,
            longitude=station.longitude,
            distance_km=round(distance_km, 3),
        )
        for station, distance_km in results
    ])


@router.get("", response_model=ApiResponse)
def list_stations(
    query: str | None = Query(default=None),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
):
    stations = service.list_stations(db, query)
    return ApiResponse(data=[StationResponse.model_validate(item) for item in stations])

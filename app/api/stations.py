from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_service
from app.schemas import ApiResponse, StationResponse
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

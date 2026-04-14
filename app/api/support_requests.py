from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies import get_current_user, get_db, get_service
from app.models import User
from app.schemas import (
    ApiResponse,
    CancelSupportRequestRequest,
    CreateSupportRequestRequest,
    UnavailableSupportRequestRequest,
    UpdateSupportRequestChecklistRequest,
    UpdateSupportRequestStatusRequest,
)
from app.service import AppService

router = APIRouter(prefix="/support-requests", tags=["support-requests"])


@router.get("", response_model=ApiResponse)
def list_support_requests(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
):
    return ApiResponse(data=service.list_support_requests(db, user))


@router.post("", response_model=ApiResponse, status_code=201)
def create_support_request(
    payload: CreateSupportRequestRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
):
    return ApiResponse(data=service.create_support_request(db, user, payload))


@router.get("/{request_id}", response_model=ApiResponse)
def get_support_request(
    request_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
):
    return ApiResponse(data=service.get_support_request(db, user, request_id))


@router.post("/{request_id}/cancel", response_model=ApiResponse)
def cancel_support_request(
    request_id: str,
    payload: CancelSupportRequestRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
):
    return ApiResponse(data=service.cancel_support_request(db, user, request_id, payload.reason))


@router.post("/{request_id}/assign", response_model=ApiResponse)
def assign_support_request(
    request_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
):
    return ApiResponse(data=service.assign_support_request(db, user, request_id))


@router.post("/{request_id}/status", response_model=ApiResponse)
def update_support_request_status(
    request_id: str,
    payload: UpdateSupportRequestStatusRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
):
    return ApiResponse(
        data=service.update_support_request_status(
            db,
            user,
            request_id,
            payload.status,
            payload.train_car_number,
            payload.completion_note,
        )
    )


@router.post("/{request_id}/checklist", response_model=ApiResponse)
def update_support_request_checklist(
    request_id: str,
    payload: UpdateSupportRequestChecklistRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
):
    return ApiResponse(
        data=service.update_support_request_checklist(
            db,
            user,
            request_id,
            payload.items,
        )
    )


@router.post("/{request_id}/unavailable", response_model=ApiResponse)
def mark_support_request_unavailable(
    request_id: str,
    payload: UnavailableSupportRequestRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
):
    return ApiResponse(data=service.mark_unavailable(db, user, request_id, payload.reason))

import re

from fastapi import APIRouter, Depends, WebSocket, WebSocketException, status
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app.dependencies import (
    get_current_user,
    get_current_websocket_session,
    get_db,
    get_service,
    get_settings,
    get_updates_hub,
)
from app.models import User
from app.realtime import SupportRequestUpdatesHub
from app.schemas import (
    ApiResponse,
    CancelSupportRequestRequest,
    CreateSupportRequestRequest,
    SupportRequestDetailResponse,
    UnavailableSupportRequestRequest,
    UpdateSupportRequestChecklistRequest,
    UpdateSupportRequestStatusRequest,
    UploadSupportRequestCurrentLocationRequest,
)
from app.service import AppService

router = APIRouter(prefix="/support-requests", tags=["support-requests"])


def _assert_allowed_websocket_origin(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    current_settings = get_settings()

    if not origin:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Origin required",
        )

    if origin in current_settings.allowed_origins:
        return

    pattern = current_settings.allowed_origin_regex
    if pattern and re.fullmatch(pattern, origin):
        return

    raise WebSocketException(
        code=status.WS_1008_POLICY_VIOLATION,
        reason="Origin not allowed",
    )


async def _broadcast_support_request_update(
    updates_hub: SupportRequestUpdatesHub,
    response: SupportRequestDetailResponse,
) -> None:
    await updates_hub.broadcast_request_updated(
        response.id,
        passenger_user_id=response.passenger_id,
        assigned_staff_user_id=response.assigned_staff_id,
        origin_station_id=response.origin_station_id,
        destination_station_id=response.destination_station_id,
        status=response.status.value,
    )


@router.websocket("/ws")
async def support_requests_websocket(
    websocket: WebSocket,
    session: tuple[User, str] = Depends(get_current_websocket_session),
    updates_hub: SupportRequestUpdatesHub = Depends(get_updates_hub),
):
    _assert_allowed_websocket_origin(websocket)
    user, session_id = session
    await updates_hub.connect(websocket, user, session_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        updates_hub.disconnect(websocket)


@router.get("", response_model=ApiResponse)
def list_support_requests(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
):
    return ApiResponse(data=service.list_support_requests(db, user))


@router.post("", response_model=ApiResponse, status_code=201)
async def create_support_request(
    payload: CreateSupportRequestRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
    updates_hub: SupportRequestUpdatesHub = Depends(get_updates_hub),
):
    response = service.create_support_request(db, user, payload)
    await _broadcast_support_request_update(updates_hub, response)
    return ApiResponse(data=response)


@router.get("/{request_id}", response_model=ApiResponse)
def get_support_request(
    request_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
):
    return ApiResponse(data=service.get_support_request(db, user, request_id))


@router.post("/{request_id}/cancel", response_model=ApiResponse)
async def cancel_support_request(
    request_id: str,
    payload: CancelSupportRequestRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
    updates_hub: SupportRequestUpdatesHub = Depends(get_updates_hub),
):
    response = service.cancel_support_request(db, user, request_id, payload.reason)
    await _broadcast_support_request_update(updates_hub, response)
    return ApiResponse(data=response)


@router.post("/{request_id}/assign", response_model=ApiResponse)
async def assign_support_request(
    request_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
    updates_hub: SupportRequestUpdatesHub = Depends(get_updates_hub),
):
    response = service.assign_support_request(db, user, request_id)
    await _broadcast_support_request_update(updates_hub, response)
    return ApiResponse(data=response)


@router.post("/{request_id}/status", response_model=ApiResponse)
async def update_support_request_status(
    request_id: str,
    payload: UpdateSupportRequestStatusRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
    updates_hub: SupportRequestUpdatesHub = Depends(get_updates_hub),
):
    response = service.update_support_request_status(
        db,
        user,
        request_id,
        payload.status,
        payload.train_number,
        payload.train_car_number,
        payload.completion_note,
    )
    await _broadcast_support_request_update(updates_hub, response)
    return ApiResponse(data=response)


@router.post("/{request_id}/checklist", response_model=ApiResponse)
async def update_support_request_checklist(
    request_id: str,
    payload: UpdateSupportRequestChecklistRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
    updates_hub: SupportRequestUpdatesHub = Depends(get_updates_hub),
):
    response = service.update_support_request_checklist(
        db,
        user,
        request_id,
        payload.items,
    )
    await _broadcast_support_request_update(updates_hub, response)
    return ApiResponse(data=response)


@router.post("/{request_id}/current-location", response_model=ApiResponse)
async def upload_support_request_current_location(
    request_id: str,
    payload: UploadSupportRequestCurrentLocationRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
    updates_hub: SupportRequestUpdatesHub = Depends(get_updates_hub),
):
    response = service.upload_current_location(db, user, request_id, payload)
    await _broadcast_support_request_update(updates_hub, response)
    return ApiResponse(data=response)


@router.post("/{request_id}/unavailable", response_model=ApiResponse)
async def mark_support_request_unavailable(
    request_id: str,
    payload: UnavailableSupportRequestRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
    updates_hub: SupportRequestUpdatesHub = Depends(get_updates_hub),
):
    response = service.mark_unavailable(db, user, request_id, payload.reason)
    await _broadcast_support_request_update(updates_hub, response)
    return ApiResponse(data=response)

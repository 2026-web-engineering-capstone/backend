import re

from fastapi import APIRouter, Depends, WebSocket, WebSocketException, status
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app.dependencies import (
    get_current_user,
    get_current_websocket_session,
    get_db,
    get_firebase_notifier,
    get_service,
    get_settings,
    get_updates_hub,
)
from app.models import User
from app.notifications import FirebaseNotifier
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


_STATUS_PUSH_LABELS = {
    "submitted": ("새 지원 요청", "출발 역에서 도움이 필요합니다."),
    "assigned": ("담당자 배정", "역무원이 만남 위치로 이동합니다."),
    "in_progress": ("역무원 도착", "만남 위치에서 안내가 시작됩니다."),
    "boarded": ("하차 처리 대기", "도착 역에서 하차 지원을 준비합니다."),
    "awaiting_dropoff": ("하차 처리 중", "하차 역에서 안내를 시작합니다."),
    "completed": ("하차 완료", "하차 지원이 완료되었어요."),
    "cancelled": ("요청이 취소되었습니다", "취소 사유가 기록되었습니다."),
    "unavailable": ("지원 불가", "현재 요청을 지원할 수 없습니다."),
}


def _notify_request_event(
    db,
    service: AppService,
    notifier: FirebaseNotifier,
    response: SupportRequestDetailResponse,
    *,
    is_new_request: bool = False,
) -> None:
    """승객/역무원 양쪽에 적절한 푸시 알림을 전송한다.

    Firebase 자격증명이 미설정이면 send_to_tokens가 no-op이라 안전하다.
    실패는 best-effort — API 응답에는 영향 없음.
    """
    if not notifier.enabled:
        return
    try:
        status_key = response.status.value
        title, body = _STATUS_PUSH_LABELS.get(
            status_key, ("지원 요청 업데이트", "요청 상태가 변경되었습니다.")
        )
        body_with_route = (
            f"{response.passenger_name}님 · {response.origin_station_name}"
            f" → {response.destination_station_name}"
        )

        # 출발 역 staff에게: 새 요청이거나 staff 액션 전 단계
        if is_new_request or status_key == "submitted":
            staff_ids = service.collect_station_staff_user_ids(
                db, response.origin_station_id
            )
            staff_tokens = service.collect_push_tokens(db, staff_ids)
            notifier.send_to_tokens(
                db,
                staff_tokens,
                title=title,
                body=body_with_route,
                data={"requestId": response.id, "type": "support_request.new"},
            )

        # 도착 역 staff에게: boarded 또는 awaiting_dropoff 진입 시
        if status_key in {"boarded", "awaiting_dropoff"} and response.destination_station_id:
            dest_staff_ids = service.collect_station_staff_user_ids(
                db, response.destination_station_id
            )
            dest_tokens = service.collect_push_tokens(db, dest_staff_ids)
            notifier.send_to_tokens(
                db,
                dest_tokens,
                title=title,
                body=body_with_route,
                data={"requestId": response.id, "type": "support_request.handoff"},
            )

        # 승객에게: assigned 이후 모든 상태 변경
        if not is_new_request and status_key != "submitted":
            passenger_tokens = service.collect_push_tokens(db, [response.passenger_id])
            notifier.send_to_tokens(
                db,
                passenger_tokens,
                title=title,
                body=body,
                data={"requestId": response.id, "type": "support_request.status"},
            )
    except Exception:  # pragma: no cover — 알림 실패는 본 응답에 영향 X
        import logging

        logging.getLogger(__name__).exception("Push notification dispatch failed")


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
    notifier: FirebaseNotifier = Depends(get_firebase_notifier),
):
    response = service.create_support_request(db, user, payload)
    await _broadcast_support_request_update(updates_hub, response)
    _notify_request_event(db, service, notifier, response, is_new_request=True)
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
    notifier: FirebaseNotifier = Depends(get_firebase_notifier),
):
    response = service.cancel_support_request(db, user, request_id, payload.reason)
    await _broadcast_support_request_update(updates_hub, response)
    _notify_request_event(db, service, notifier, response)
    return ApiResponse(data=response)


@router.post("/{request_id}/assign", response_model=ApiResponse)
async def assign_support_request(
    request_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
    updates_hub: SupportRequestUpdatesHub = Depends(get_updates_hub),
    notifier: FirebaseNotifier = Depends(get_firebase_notifier),
):
    response = service.assign_support_request(db, user, request_id)
    await _broadcast_support_request_update(updates_hub, response)
    _notify_request_event(db, service, notifier, response)
    return ApiResponse(data=response)


@router.post("/{request_id}/status", response_model=ApiResponse)
async def update_support_request_status(
    request_id: str,
    payload: UpdateSupportRequestStatusRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
    updates_hub: SupportRequestUpdatesHub = Depends(get_updates_hub),
    notifier: FirebaseNotifier = Depends(get_firebase_notifier),
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
    _notify_request_event(db, service, notifier, response)
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
    # 체크리스트 update는 상태 변경이 아니라 푸시 알림 미발송 (WebSocket 갱신만)
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
    notifier: FirebaseNotifier = Depends(get_firebase_notifier),
):
    response = service.mark_unavailable(db, user, request_id, payload.reason)
    await _broadcast_support_request_update(updates_hub, response)
    _notify_request_event(db, service, notifier, response)
    return ApiResponse(data=response)

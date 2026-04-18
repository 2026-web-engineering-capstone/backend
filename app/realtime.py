from __future__ import annotations

from dataclasses import dataclass

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.enums import Role, SupportRequestStatus
from app.models import User


@dataclass(frozen=True)
class SupportRequestSubscription:
    websocket: WebSocket
    session_id: str
    user_id: str
    role: str
    station_id: str | None


class SupportRequestUpdatesHub:
    def __init__(self) -> None:
        self._connections: dict[WebSocket, SupportRequestSubscription] = {}

    async def connect(self, websocket: WebSocket, user: User, session_id: str) -> None:
        await websocket.accept()
        self._connections[websocket] = SupportRequestSubscription(
            websocket=websocket,
            session_id=session_id,
            user_id=user.id,
            role=user.role.value,
            station_id=user.station_id,
        )

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.pop(websocket, None)

    async def disconnect_session(self, session_id: str) -> None:
        for websocket, subscription in tuple(self._connections.items()):
            if subscription.session_id == session_id:
                self.disconnect(websocket)
                try:
                    await websocket.close(code=1008, reason='Session closed')
                except RuntimeError:
                    continue

    async def broadcast_request_updated(
        self,
        request_id: str,
        *,
        passenger_user_id: str,
        assigned_staff_user_id: str | None,
        origin_station_id: str,
        destination_station_id: str,
        status: str,
    ) -> None:
        payload = {
            "type": "support_request.updated",
            "requestId": request_id,
        }
        for subscription in tuple(self._connections.values()):
            if not self._can_receive(
                subscription,
                passenger_user_id=passenger_user_id,
                assigned_staff_user_id=assigned_staff_user_id,
                origin_station_id=origin_station_id,
                destination_station_id=destination_station_id,
                status=status,
            ):
                continue
            try:
                await subscription.websocket.send_json(payload)
            except (RuntimeError, WebSocketDisconnect):
                self.disconnect(subscription.websocket)

    def _can_receive(
        self,
        subscription: SupportRequestSubscription,
        *,
        passenger_user_id: str,
        assigned_staff_user_id: str | None,
        origin_station_id: str,
        destination_station_id: str,
        status: str,
    ) -> bool:
        if subscription.role == Role.PASSENGER.value:
            return subscription.user_id == passenger_user_id
        if subscription.role != Role.STAFF.value:
            return False
        if subscription.user_id == assigned_staff_user_id:
            return True
        if subscription.station_id == origin_station_id and status in {
            SupportRequestStatus.SUBMITTED.value,
            SupportRequestStatus.ASSIGNED.value,
            SupportRequestStatus.IN_PROGRESS.value,
            SupportRequestStatus.BOARDED.value,
            SupportRequestStatus.AWAITING_DROPOFF.value,
        }:
            return True
        return subscription.station_id == destination_station_id and status in {
            SupportRequestStatus.BOARDED.value,
            SupportRequestStatus.AWAITING_DROPOFF.value,
        }

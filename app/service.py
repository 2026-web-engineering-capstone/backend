from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.enums import (
    CancelReason,
    Role,
    SupportRequestStatus,
    SupportType,
    UnavailableReason,
)
from app.models import (
    Station,
    SupportRequest,
    SupportRequestChecklistItem,
    SupportRequestCurrentLocation,
    SupportRequestEvent,
    SupportRequestSupportType,
    User,
    UserPushToken,
)
from app.schemas import (
    CreateSupportRequestRequest,
    SupportRequestChecklistItemRequest,
    SupportRequestChecklistItemResponse,
    SupportRequestCurrentLocationResponse,
    SupportRequestDetailResponse,
    SupportRequestEventResponse,
    SupportRequestListItem,
)

CANCEL_REASON_ALIASES: dict[str, tuple[CancelReason, str]] = {
    "change_of_plans": (CancelReason.CHANGE_OF_PLANS, "일정 변경"),
    "일정 변경": (CancelReason.CHANGE_OF_PLANS, "일정 변경"),
    "duplicate_request": (CancelReason.DUPLICATE_REQUEST, "중복 요청"),
    "중복 요청": (CancelReason.DUPLICATE_REQUEST, "중복 요청"),
    "no_longer_needed": (CancelReason.NO_LONGER_NEEDED, "도움이 더 이상 필요하지 않음"),
    "도움이 더 이상 필요하지 않음": (CancelReason.NO_LONGER_NEEDED, "도움이 더 이상 필요하지 않음"),
}

UNAVAILABLE_REASON_ALIASES: dict[str, tuple[UnavailableReason, str]] = {
    "no_show": (UnavailableReason.NO_SHOW, "승객이 나타나지 않음"),
    "승객이 나타나지 않음": (UnavailableReason.NO_SHOW, "승객이 나타나지 않음"),
    "urgent_duty": (UnavailableReason.URGENT_DUTY, "긴급 업무 대응"),
    "긴급 업무 대응": (UnavailableReason.URGENT_DUTY, "긴급 업무 대응"),
    "support_unavailable": (UnavailableReason.SUPPORT_UNAVAILABLE, "현장 인력 부족"),
    "현장 인력 부족": (UnavailableReason.SUPPORT_UNAVAILABLE, "현장 인력 부족"),
}


TERMINAL_STATUSES = {
    SupportRequestStatus.CANCELLED,
    SupportRequestStatus.UNAVAILABLE,
    SupportRequestStatus.COMPLETED,
}

ALLOWED_TRANSITIONS: dict[SupportRequestStatus, set[SupportRequestStatus]] = {
    SupportRequestStatus.SUBMITTED: {
        SupportRequestStatus.ASSIGNED,
        SupportRequestStatus.CANCELLED,
        SupportRequestStatus.UNAVAILABLE,
    },
    SupportRequestStatus.ASSIGNED: {
        SupportRequestStatus.IN_PROGRESS,
        SupportRequestStatus.CANCELLED,
        SupportRequestStatus.UNAVAILABLE,
    },
    SupportRequestStatus.IN_PROGRESS: {
        SupportRequestStatus.BOARDED,
        SupportRequestStatus.UNAVAILABLE,
    },
    SupportRequestStatus.BOARDED: {SupportRequestStatus.AWAITING_DROPOFF},
    SupportRequestStatus.AWAITING_DROPOFF: {SupportRequestStatus.COMPLETED},
    SupportRequestStatus.COMPLETED: set(),
    SupportRequestStatus.CANCELLED: set(),
    SupportRequestStatus.UNAVAILABLE: set(),
}

STATION_SEED = [
    ("STN-GY", "계양역", "인천1호선", "#3681cb"),
    ("STN-GH", "귤현역", "인천1호선", "#3681cb"),
    ("STN-BC", "박촌역", "인천1호선", "#3681cb"),
    ("STN-IH", "임학역", "인천1호선", "#3681cb"),
    ("STN-JJ", "작전역", "인천1호선", "#3681cb"),
    ("STN-GS", "갈산역", "인천1호선", "#3681cb"),
    ("STN-JI", "지식정보단지역", "인천1호선", "#3681cb"),
    ("STN-ICU", "인천대입구역", "인천1호선", "#3681cb"),
    ("STN-CP", "센트럴파크역", "인천1호선", "#3681cb"),
    ("STN-IBD", "국제업무지구역", "인천1호선", "#3681cb"),
    ("STN-SD", "송도달빛축제공원역", "인천1호선", "#3681cb"),
]

USER_SEED = [
    {
        "id": "USR-PASSENGER-DEMO",
        "name": "김교움",
        "email": "passenger@gyoum.kr",
        "role": Role.PASSENGER,
        "station_id": None,
    },
    {
        "id": "USR-STAFF-DEMO",
        "name": "김민수",
        "email": "staff@gyoum.kr",
        "role": Role.STAFF,
        "station_id": "STN-ICU",
    },
    {
        "id": "USR-DRIVER-DEMO",
        "name": "박기관",
        "email": "driver@gyoum.kr",
        "role": Role.DRIVER,
        "station_id": None,
    },
    {
        "id": "USR-ADMIN-DEMO",
        "name": "관리자",
        "email": "admin@gyoum.kr",
        "role": Role.ADMIN,
        "station_id": None,
    },
]

CHECKLIST_TEMPLATES: dict[SupportType, list[tuple[str, str]]] = {
    SupportType.WHEELCHAIR: [
        ("prepare-wheelchair-ramp", "휠체어 승하차 발판을 준비했어요."),
        ("check-wheelchair-route", "엘리베이터와 이동 동선을 확인했어요."),
    ],
    SupportType.VISUAL_GUIDE: [
        ("greet-passenger", "만남 위치에서 승객을 확인했어요."),
        ("guide-to-platform", "승강장과 하차 동선을 안내할 준비를 마쳤어요."),
    ],
    SupportType.BOARDING_RAMP: [
        ("prepare-boarding-support", "승하차 보조 장비와 위치를 확인했어요."),
        ("share-boarding-position", "탑승 위치와 열차 칸 정보를 확인했어요."),
    ],
}


@dataclass
class AppService:
    db_factory: Callable[[], Session]

    def seed(self) -> None:
        session = self.db_factory()
        try:
            if session.scalar(select(Station.id).limit(1)) is None:
                session.add_all(
                    [
                        Station(id=station_id, name=name, line=line, line_color=line_color)
                        for station_id, name, line, line_color in STATION_SEED
                    ]
                )
            if session.scalar(select(User.id).limit(1)) is None:
                session.add_all([User(**item) for item in USER_SEED])
            session.commit()
        finally:
            session.close()

    def list_stations(self, db: Session, query: str | None) -> list[Station]:
        stmt = select(Station).order_by(Station.name)
        if query:
            stmt = stmt.where(Station.name.contains(query))
        return list(db.scalars(stmt))

    def get_demo_user_for_role(self, db: Session, role: Role) -> User:
        stmt = select(User).where(User.role == role).limit(1)
        user = db.scalar(stmt)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    def register_push_token(
        self,
        db: Session,
        actor: User,
        token: str,
        platform: str,
        installation_id: str,
    ) -> None:
        existing_installation = db.scalar(
            select(UserPushToken).where(UserPushToken.installation_id == installation_id)
        )
        existing_token = db.scalar(
            select(UserPushToken).where(UserPushToken.token == token)
        )

        if existing_token and existing_token.installation_id is None:
            existing_token.installation_id = installation_id
            existing_installation = existing_token

        if existing_installation:
            if existing_installation.user_id != actor.id and existing_installation.token != token:
                raise HTTPException(status_code=409, detail="Push token already registered")
        elif existing_token:
            raise HTTPException(status_code=409, detail="Push token already registered")

        try:
            if existing_installation:
                existing_installation.user_id = actor.id
                existing_installation.token = token
                existing_installation.platform = platform
            else:
                db.add(
                    UserPushToken(
                        user_id=actor.id,
                        installation_id=installation_id,
                        token=token,
                        platform=platform,
                    )
                )
            db.commit()
        except IntegrityError as error:
            db.rollback()
            raise HTTPException(status_code=409, detail="Push token already registered") from error

    def unregister_push_token(
        self,
        db: Session,
        actor: User,
        installation_id: str,
        token: str | None = None,
    ) -> None:
        self.unregister_push_token_for_user(db, actor.id, installation_id, token)

    def unregister_push_token_for_user(
        self,
        db: Session,
        user_id: str,
        installation_id: str,
        token: str | None = None,
    ) -> None:
        conditions = [
            UserPushToken.user_id == user_id,
            UserPushToken.installation_id == installation_id,
        ]
        if token:
            conditions.append(UserPushToken.token == token)

        push_token = db.scalar(select(UserPushToken).where(*conditions))
        if not push_token:
            db.commit()
            return

        db.delete(push_token)
        db.commit()

    def unregister_push_token_for_installation(
        self,
        db: Session,
        installation_id: str,
        token: str,
    ) -> None:
        push_token = db.scalar(
            select(UserPushToken).where(
                UserPushToken.installation_id == installation_id,
                UserPushToken.token == token,
            )
        )

        if not push_token:
            db.commit()
            return

        db.delete(push_token)
        db.commit()

    def list_support_requests(self, db: Session, actor: User) -> list[SupportRequestListItem]:
        stmt = self._base_request_query(include_current_locations=False)
        if actor.role == Role.PASSENGER:
            stmt = stmt.where(SupportRequest.passenger_user_id == actor.id)
        elif actor.role == Role.STAFF:
            visible_statuses = {
                SupportRequestStatus.SUBMITTED,
                SupportRequestStatus.ASSIGNED,
                SupportRequestStatus.IN_PROGRESS,
                SupportRequestStatus.BOARDED,
                SupportRequestStatus.AWAITING_DROPOFF,
            }
            stmt = stmt.where(
                (SupportRequest.assigned_staff_user_id == actor.id)
                | (
                    (SupportRequest.origin_station_id == actor.station_id)
                    & SupportRequest.status.in_(visible_statuses)
                )
                | (
                    (SupportRequest.destination_station_id == actor.station_id)
                    & SupportRequest.status.in_(
                        [
                            SupportRequestStatus.BOARDED,
                            SupportRequestStatus.AWAITING_DROPOFF,
                        ]
                    )
                )
            )
        else:
            raise HTTPException(status_code=403, detail="Unsupported role")
        requests = list(db.scalars(stmt.order_by(SupportRequest.created_at.desc())).unique())
        return [self._to_list_response(item) for item in requests]

    def get_support_request(self, db: Session, actor: User, request_id: str) -> SupportRequestDetailResponse:
        support_request = db.scalar(
            self._base_request_query(include_current_locations=True).where(SupportRequest.id == request_id)
        )
        if not support_request:
            raise HTTPException(status_code=404, detail="Support request not found")
        self._assert_can_view(actor, support_request)
        return self._to_detail_response(db, actor, support_request)

    def create_support_request(
        self,
        db: Session,
        actor: User,
        payload: CreateSupportRequestRequest,
    ) -> SupportRequestDetailResponse:
        if actor.role != Role.PASSENGER:
            raise HTTPException(status_code=403, detail="Only passengers can create requests")
        if payload.origin_station_id == payload.destination_station_id:
            raise HTTPException(status_code=422, detail="Origin and destination must differ")
        self._require_station(db, payload.origin_station_id)
        self._require_station(db, payload.destination_station_id)

        support_request = SupportRequest(
            passenger_user_id=actor.id,
            origin_station_id=payload.origin_station_id,
            destination_station_id=payload.destination_station_id,
            meeting_point=payload.meeting_point,
            notes=payload.notes,
            status=SupportRequestStatus.SUBMITTED,
        )
        support_request.support_types = [
            SupportRequestSupportType(support_type=support_type)
            for support_type in payload.support_types
        ]
        support_request.checklist_items = self._build_default_checklist_items(payload.support_types)
        db.add(support_request)
        db.flush()
        self._append_event(
            db,
            support_request=support_request,
            actor=actor,
            event_type="created",
            to_status=SupportRequestStatus.SUBMITTED,
            message="지원 요청이 접수되었습니다.",
        )
        db.commit()
        db.refresh(support_request)
        return self.get_support_request(db, actor, support_request.id)

    def cancel_support_request(
        self,
        db: Session,
        actor: User,
        request_id: str,
        reason: str,
    ) -> SupportRequestDetailResponse:
        support_request = self._get_request_entity(db, request_id)
        if actor.role != Role.PASSENGER or support_request.passenger_user_id != actor.id:
            raise HTTPException(status_code=403, detail="Only the passenger can cancel this request")
        cancel_reason, message = self._parse_cancel_reason(reason)
        self._transition_request(
            db,
            support_request=support_request,
            actor=actor,
            next_status=SupportRequestStatus.CANCELLED,
            message=message,
            cancel_reason=cancel_reason,
        )
        db.commit()
        return self._reload_request_detail(db, actor, request_id)

    def assign_support_request(self, db: Session, actor: User, request_id: str) -> SupportRequestDetailResponse:
        support_request = self._get_request_entity(db, request_id)
        self._assert_staff(actor)
        if actor.station_id != support_request.origin_station_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        support_request.assigned_staff_user_id = actor.id
        self._transition_request(
            db,
            support_request=support_request,
            actor=actor,
            next_status=SupportRequestStatus.ASSIGNED,
            message=f"{actor.name} 역무원이 배정되었습니다.",
        )
        db.commit()
        return self.get_support_request(db, actor, request_id)

    def update_support_request_checklist(
        self,
        db: Session,
        actor: User,
        request_id: str,
        items: list[SupportRequestChecklistItemRequest],
    ) -> SupportRequestDetailResponse:
        support_request = self._get_request_entity(db, request_id)
        self._assert_status_actor(actor, support_request)
        if support_request.status in TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail="Checklist cannot be updated")
        existing_items_by_code = {
            item.code: item for item in support_request.checklist_items
        }
        submitted_codes = [item.code for item in items]

        if len(items) != len(existing_items_by_code) or set(submitted_codes) != set(
            existing_items_by_code
        ):
            raise HTTPException(status_code=422, detail="Invalid checklist items")

        for item in items:
            existing_item = existing_items_by_code[item.code]
            if item.label != existing_item.label:
                raise HTTPException(status_code=422, detail="Invalid checklist items")
            existing_item.checked = item.checked

        db.commit()
        return self._reload_request_detail(db, actor, request_id)

    def update_support_request_status(
        self,
        db: Session,
        actor: User,
        request_id: str,
        next_status: SupportRequestStatus,
        train_car_number: str | None,
        completion_note: str | None,
    ) -> SupportRequestDetailResponse:
        support_request = self._get_request_entity(db, request_id)
        self._assert_status_actor(actor, support_request)

        normalized_train_car_number = train_car_number.strip() if train_car_number else None
        normalized_completion_note = completion_note.strip() if completion_note else None

        if next_status == SupportRequestStatus.BOARDED:
            if not normalized_train_car_number:
                raise HTTPException(status_code=422, detail="Train car number is required")
        elif normalized_train_car_number is not None:
            raise HTTPException(status_code=422, detail="Train car number is only allowed for boarded status")

        if next_status == SupportRequestStatus.COMPLETED:
            if next_status in ALLOWED_TRANSITIONS[support_request.status] and not normalized_completion_note:
                raise HTTPException(status_code=422, detail="Completion note is required")
        elif normalized_completion_note is not None:
            raise HTTPException(status_code=422, detail="Completion note is only allowed for completed status")

        if normalized_train_car_number:
            support_request.train_car_number = normalized_train_car_number
        self._transition_request(
            db,
            support_request=support_request,
            actor=actor,
            next_status=next_status,
            message=(
                normalized_completion_note
                if next_status == SupportRequestStatus.COMPLETED
                else self._status_message(next_status)
            ),
            completion_note=normalized_completion_note,
        )
        db.commit()
        return self._reload_request_detail(db, actor, request_id)

    def mark_unavailable(
        self,
        db: Session,
        actor: User,
        request_id: str,
        reason: str,
    ) -> SupportRequestDetailResponse:
        support_request = self._get_request_entity(db, request_id)
        self._assert_status_actor(actor, support_request)
        unavailable_reason, message = self._parse_unavailable_reason(reason)
        self._transition_request(
            db,
            support_request=support_request,
            actor=actor,
            next_status=SupportRequestStatus.UNAVAILABLE,
            message=message,
            unavailable_reason=unavailable_reason,
        )
        db.commit()
        return self._reload_request_detail(db, actor, request_id)

    def _base_request_query(
        self, include_current_locations: bool
    ) -> Select[tuple[SupportRequest]]:
        options = [
            joinedload(SupportRequest.passenger),
            joinedload(SupportRequest.assigned_staff),
            joinedload(SupportRequest.origin_station),
            joinedload(SupportRequest.destination_station),
            selectinload(SupportRequest.support_types),
            selectinload(SupportRequest.checklist_items),
            selectinload(SupportRequest.events).joinedload(SupportRequestEvent.actor),
        ]
        return select(SupportRequest).options(*options)

    def _get_request_entity(self, db: Session, request_id: str) -> SupportRequest:
        support_request = db.scalar(
            self._base_request_query(include_current_locations=False).where(SupportRequest.id == request_id)
        )
        if not support_request:
            raise HTTPException(status_code=404, detail="Support request not found")
        return support_request

    def _reload_request_detail(
        self, db: Session, actor: User, request_id: str
    ) -> SupportRequestDetailResponse:
        db.expire_all()
        support_request = db.scalar(
            self._base_request_query(include_current_locations=True)
            .execution_options(populate_existing=True)
            .where(SupportRequest.id == request_id)
        )
        if not support_request:
            raise HTTPException(status_code=404, detail="Support request not found")
        return self._to_detail_response(db, actor, support_request)

    def _transition_request(
        self,
        db: Session,
        support_request: SupportRequest,
        actor: User,
        next_status: SupportRequestStatus,
        message: str,
        cancel_reason: CancelReason | None = None,
        unavailable_reason: UnavailableReason | None = None,
        completion_note: str | None = None,
    ) -> None:
        current_status = support_request.status
        allowed = ALLOWED_TRANSITIONS[current_status]
        if next_status not in allowed:
            raise HTTPException(status_code=409, detail="Invalid status transition")
        support_request.status = next_status
        if cancel_reason:
            support_request.cancel_reason = cancel_reason
        if unavailable_reason:
            support_request.unavailable_reason = unavailable_reason
        if completion_note:
            support_request.completion_note = completion_note
        self._append_event(
            db,
            support_request=support_request,
            actor=actor,
            event_type="status_changed",
            from_status=current_status,
            to_status=next_status,
            message=message,
        )

    def _build_default_checklist_items(
        self, support_types: list[SupportType]
    ) -> list[SupportRequestChecklistItem]:
        seen_codes: set[str] = set()
        items: list[SupportRequestChecklistItem] = []
        for support_type in support_types:
            for code, label in CHECKLIST_TEMPLATES.get(support_type, []):
                if code in seen_codes:
                    continue
                seen_codes.add(code)
                items.append(
                    SupportRequestChecklistItem(
                        code=code,
                        label=label,
                        checked=False,
                    )
                )
        return items

    def _append_event(
        self,
        db: Session,
        support_request: SupportRequest,
        actor: User,
        event_type: str,
        message: str,
        from_status: SupportRequestStatus | None = None,
        to_status: SupportRequestStatus | None = None,
    ) -> None:
        event = SupportRequestEvent(
            request_id=support_request.id,
            actor_user_id=actor.id,
            type=event_type,
            from_status=from_status,
            to_status=to_status,
            message=message,
        )
        db.add(event)

    def _parse_cancel_reason(self, reason: str) -> tuple[CancelReason, str]:
        normalized_reason = reason.strip()
        matched = CANCEL_REASON_ALIASES.get(normalized_reason)
        if matched:
            return matched
        raise HTTPException(status_code=422, detail="Invalid cancel reason")

    def _parse_unavailable_reason(self, reason: str) -> tuple[UnavailableReason, str]:
        normalized_reason = reason.strip()
        matched = UNAVAILABLE_REASON_ALIASES.get(normalized_reason)
        if matched:
            return matched
        raise HTTPException(status_code=422, detail="Invalid unavailable reason")

    def _normalize_cancel_reason(self, cancel_reason: str | None) -> CancelReason | None:
        if not cancel_reason:
            return None

        matched = CANCEL_REASON_ALIASES.get(cancel_reason)
        if matched:
            return matched[0]

        try:
            return CancelReason(cancel_reason)
        except ValueError:
            return None

    def _normalize_unavailable_reason(
        self, unavailable_reason: str | None
    ) -> UnavailableReason | None:
        if not unavailable_reason:
            return None

        matched = UNAVAILABLE_REASON_ALIASES.get(unavailable_reason)
        if matched:
            return matched[0]

        try:
            return UnavailableReason(unavailable_reason)
        except ValueError:
            return None

    def _get_latest_current_location(
        self, db: Session, request_id: str, passenger_user_id: str
    ) -> SupportRequestCurrentLocation | None:
        return db.scalar(
            select(SupportRequestCurrentLocation)
            .where(SupportRequestCurrentLocation.request_id == request_id)
            .where(SupportRequestCurrentLocation.passenger_user_id == passenger_user_id)
            .order_by(
                SupportRequestCurrentLocation.recorded_at.desc().nulls_last(),
                SupportRequestCurrentLocation.id.desc(),
            )
        )

    def _to_current_location_response(
        self, db: Session, support_request: SupportRequest
    ) -> SupportRequestCurrentLocationResponse | None:
        latest_location = self._get_latest_current_location(
            db,
            request_id=support_request.id,
            passenger_user_id=support_request.passenger_user_id,
        )
        if latest_location is None:
            return None

        return SupportRequestCurrentLocationResponse(
            latitude=latest_location.latitude,
            longitude=latest_location.longitude,
            accuracy_meters=latest_location.accuracy_meters,
            recorded_at=latest_location.recorded_at,
        )

    def _should_include_current_location(
        self, actor: User, support_request: SupportRequest
    ) -> bool:
        return (
            actor.role == Role.STAFF
            and actor.station_id == support_request.origin_station_id
            and support_request.status
            in {
                SupportRequestStatus.SUBMITTED,
                SupportRequestStatus.ASSIGNED,
                SupportRequestStatus.IN_PROGRESS,
            }
        )

    def _to_list_response(self, support_request: SupportRequest) -> SupportRequestListItem:
        return SupportRequestListItem(
            id=support_request.id,
            status=support_request.status,
            origin_station_id=support_request.origin_station_id,
            origin_station_name=support_request.origin_station.name,
            destination_station_id=support_request.destination_station_id,
            destination_station_name=support_request.destination_station.name,
            support_types=[item.support_type for item in support_request.support_types],
            meeting_point=support_request.meeting_point,
            notes=support_request.notes,
            passenger_name=support_request.passenger.name,
            assigned_staff_name=support_request.assigned_staff.name if support_request.assigned_staff else None,
            train_car_number=support_request.train_car_number,
            created_at=support_request.created_at,
            cancel_reason=self._normalize_cancel_reason(support_request.cancel_reason),
            unavailable_reason=self._normalize_unavailable_reason(
                support_request.unavailable_reason
            ),
            completion_note=support_request.completion_note,
        )

    def _to_detail_response(
        self, db: Session, actor: User, support_request: SupportRequest
    ) -> SupportRequestDetailResponse:
        list_response = self._to_list_response(support_request)
        current_location = None
        if self._should_include_current_location(actor, support_request):
            current_location = self._to_current_location_response(db, support_request)

        return SupportRequestDetailResponse(
            **list_response.model_dump(),
            passenger_id=support_request.passenger_user_id,
            assigned_staff_id=support_request.assigned_staff_user_id,
            current_location=current_location,
            checklist_items=[
                SupportRequestChecklistItemResponse(
                    id=item.id,
                    code=item.code,
                    label=item.label,
                    checked=item.checked,
                )
                for item in support_request.checklist_items
            ],
            events=[
                SupportRequestEventResponse(
                    id=event.id,
                    type=event.type,
                    actor_name=event.actor.name,
                    from_status=event.from_status,
                    to_status=event.to_status,
                    message=event.message,
                    created_at=event.created_at,
                )
                for event in support_request.events
            ],
        )

    def _require_station(self, db: Session, station_id: str) -> Station:
        station = db.get(Station, station_id)
        if not station:
            raise HTTPException(status_code=404, detail=f"Station not found: {station_id}")
        return station

    def _assert_can_view(self, actor: User, support_request: SupportRequest) -> None:
        if actor.role == Role.PASSENGER and support_request.passenger_user_id != actor.id:
            raise HTTPException(status_code=403, detail="Forbidden")
        if actor.role == Role.STAFF:
            if support_request.assigned_staff_user_id == actor.id:
                return
            if actor.station_id == support_request.origin_station_id and support_request.status in {
                SupportRequestStatus.SUBMITTED,
                SupportRequestStatus.ASSIGNED,
                SupportRequestStatus.IN_PROGRESS,
                SupportRequestStatus.BOARDED,
                SupportRequestStatus.AWAITING_DROPOFF,
            }:
                return
            if actor.station_id == support_request.destination_station_id and support_request.status in {
                SupportRequestStatus.BOARDED,
                SupportRequestStatus.AWAITING_DROPOFF,
            }:
                return
            raise HTTPException(status_code=403, detail="Forbidden")
        if actor.role not in {Role.PASSENGER, Role.STAFF}:
            raise HTTPException(status_code=403, detail="Unsupported role")

    def _assert_staff(self, actor: User) -> None:
        if actor.role != Role.STAFF:
            raise HTTPException(status_code=403, detail="Only staff can perform this action")

    def _assert_status_actor(self, actor: User, support_request: SupportRequest) -> None:
        self._assert_staff(actor)
        if support_request.status in {
            SupportRequestStatus.BOARDED,
            SupportRequestStatus.AWAITING_DROPOFF,
        }:
            if actor.station_id == support_request.destination_station_id:
                return
            raise HTTPException(status_code=403, detail="Request must be handled by destination staff")
        if support_request.assigned_staff_user_id == actor.id:
            return
        raise HTTPException(status_code=403, detail="Request must be assigned to current staff")

    def _status_message(self, status_value: SupportRequestStatus) -> str:
        messages = {
            SupportRequestStatus.ASSIGNED: "역무원이 배정되었습니다.",
            SupportRequestStatus.IN_PROGRESS: "역무원이 만남 위치로 이동하고 있습니다.",
            SupportRequestStatus.BOARDED: "승차가 완료되었습니다.",
            SupportRequestStatus.AWAITING_DROPOFF: "하차 역에서 지원 준비 중입니다.",
            SupportRequestStatus.COMPLETED: "지원이 완료되었습니다.",
        }
        return messages.get(status_value, status_value.value)


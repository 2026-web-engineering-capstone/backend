from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import Select, select, text
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
    UploadSupportRequestCurrentLocationRequest,
)
from app.station_catalog import (
    load_arrival_station_catalog,
    normalize_line_name,
    normalize_station_name,
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

LOCATION_UPLOAD_ALLOWED_STATUSES = {
    SupportRequestStatus.SUBMITTED,
    SupportRequestStatus.ASSIGNED,
    SupportRequestStatus.IN_PROGRESS,
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

_LINE_SEOUL_1 = ("1호선", "#0052A4")
_LINE_SEOUL_2 = ("2호선", "#00A84D")
_LINE_SEOUL_3 = ("3호선", "#EF7C1C")
_LINE_SEOUL_4 = ("4호선", "#00A4E3")
_LINE_SEOUL_5 = ("5호선", "#996CAC")
_LINE_SEOUL_6 = ("6호선", "#CD7C2F")
_LINE_SEOUL_7 = ("7호선", "#747F00")
_LINE_GYEONGUI_JUNGANG = ("경의중앙선", "#77C4A3")
_LINE_SUIN_BUNDANG = ("수인분당선", "#F5A200")
_LINE_AIRPORT = ("공항철도", "#0090D2")

STATION_SEED = [
    # 서울 4호선 전체 (당고개 ~ 오이도 48개)
    ("STN-DGG", "당고개역", *_LINE_SEOUL_4),
    ("STN-SGE", "상계역", *_LINE_SEOUL_4),
    ("STN-NWN", "노원역", *_LINE_SEOUL_4),
    ("STN-CDG", "창동역", *_LINE_SEOUL_4),
    ("STN-SMM", "쌍문역", *_LINE_SEOUL_4),
    ("STN-SYU", "수유역", *_LINE_SEOUL_4),
    ("STN-MIA", "미아역", *_LINE_SEOUL_4),
    ("STN-MSG", "미아사거리역", *_LINE_SEOUL_4),
    ("STN-GIM", "길음역", *_LINE_SEOUL_4),
    ("STN-SSW", "성신여대입구역", *_LINE_SEOUL_4),
    ("STN-HSU", "한성대입구역", *_LINE_SEOUL_4),
    ("STN-HYE", "혜화역", *_LINE_SEOUL_4),
    ("STN-DDM", "동대문역", *_LINE_SEOUL_4),
    ("STN-DDH", "동대문역사문화공원역", *_LINE_SEOUL_4),
    ("STN-CMR", "충무로역", *_LINE_SEOUL_4),
    ("STN-MDG", "명동역", *_LINE_SEOUL_4),
    ("STN-HOH", "회현역", *_LINE_SEOUL_4),
    ("STN-SLY", "서울역", *_LINE_SEOUL_4),
    ("STN-SUK", "숙대입구역", *_LINE_SEOUL_4),
    ("STN-SGJ", "삼각지역", *_LINE_SEOUL_4),
    ("STN-SYS", "신용산역", *_LINE_SEOUL_4),
    ("STN-ICN", "이촌역", *_LINE_SEOUL_4),
    ("STN-DJK", "동작역", *_LINE_SEOUL_4),
    ("STN-CSD", "총신대입구역", *_LINE_SEOUL_4),
    ("STN-SDG", "사당역", *_LINE_SEOUL_4),
    ("STN-NTR", "남태령역", *_LINE_SEOUL_4),
    ("STN-SBW", "선바위역", *_LINE_SEOUL_4),
    ("STN-GMP", "경마공원역", *_LINE_SEOUL_4),
    ("STN-DGW", "대공원역", *_LINE_SEOUL_4),
    ("STN-GCN", "과천역", *_LINE_SEOUL_4),
    ("STN-GCH", "정부과천청사역", *_LINE_SEOUL_4),
    ("STN-IDW", "인덕원역", *_LINE_SEOUL_4),
    ("STN-PCN", "평촌역", *_LINE_SEOUL_4),
    ("STN-BGE", "범계역", *_LINE_SEOUL_4),
    ("STN-GMJ", "금정역", *_LINE_SEOUL_4),
    ("STN-SBN", "산본역", *_LINE_SEOUL_4),
    ("STN-SRS", "수리산역", *_LINE_SEOUL_4),
    ("STN-DYM", "대야미역", *_LINE_SEOUL_4),
    ("STN-BWL", "반월역", *_LINE_SEOUL_4),
    ("STN-SNS", "상록수역", *_LINE_SEOUL_4),
    ("STN-HDA", "한대앞역", *_LINE_SEOUL_4),
    ("STN-JAN", "중앙역", *_LINE_SEOUL_4),
    ("STN-GJN", "고잔역", *_LINE_SEOUL_4),
    ("STN-CZI", "초지역", *_LINE_SEOUL_4),
    ("STN-ANS", "안산역", *_LINE_SEOUL_4),
    ("STN-SGO", "신길온천역", *_LINE_SEOUL_4),
    ("STN-JWG", "정왕역", *_LINE_SEOUL_4),
    ("STN-ODO", "오이도역", *_LINE_SEOUL_4),
    # 주요 환승역은 노선별 선택/표시가 가능하도록 별도 station_id로 보강.
    ("STN-DDM-L1", "동대문역", *_LINE_SEOUL_1),
    ("STN-DDH-L2", "동대문역사문화공원역", *_LINE_SEOUL_2),
    ("STN-DDH-L5", "동대문역사문화공원역", *_LINE_SEOUL_5),
    ("STN-CMR-L3", "충무로역", *_LINE_SEOUL_3),
    ("STN-SLY-L1", "서울역", *_LINE_SEOUL_1),
    ("STN-SLY-GJ", "서울역", *_LINE_GYEONGUI_JUNGANG),
    ("STN-SLY-AR", "서울역", *_LINE_AIRPORT),
    ("STN-SGJ-L6", "삼각지역", *_LINE_SEOUL_6),
    ("STN-ICN-GJ", "이촌역", *_LINE_GYEONGUI_JUNGANG),
    ("STN-SDG-L2", "사당역", *_LINE_SEOUL_2),
    ("STN-NWN-L7", "노원역", *_LINE_SEOUL_7),
    ("STN-CDG-L1", "창동역", *_LINE_SEOUL_1),
    ("STN-GMJ-L1", "금정역", *_LINE_SEOUL_1),
    ("STN-ODO-SB", "오이도역", *_LINE_SUIN_BUNDANG),
]


def _build_station_seed() -> list[tuple[str, str, str, str]]:
    seed = list(STATION_SEED)
    seen = {
        (normalize_station_name(name), normalize_line_name(line))
        for _station_id, name, line, _line_color in seed
    }

    for item in load_arrival_station_catalog():
        key = (normalize_station_name(item["name"]), normalize_line_name(item["line"]))
        if key in seen:
            continue
        seed.append(
            (
                f"STN-{item['subway_id']}-{item['station_id']}",
                item["name"],
                item["line"],
                item["line_color"],
            )
        )
        seen.add(key)

    return seed


ALL_STATION_SEED = _build_station_seed()
VISIBLE_STATION_IDS = {station_id for station_id, _name, _line, _color in ALL_STATION_SEED}

USER_SEED = [
    {
        "id": "USR-PASSENGER-DEMO",
        "name": "김교움",
        "email": "passenger@gyoum.kr",
        "role": Role.PASSENGER,
        "station_id": None,
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


def _build_staff_seed() -> list[dict[str, object]]:
    """모든 시드 역에 1명씩 데모 staff 자동 생성."""
    staff: list[dict[str, object]] = []
    for station_id, station_name, _line, _line_color in ALL_STATION_SEED:
        suffix = station_id.removeprefix("STN-")
        staff.append(
            {
                "id": f"USR-STAFF-{suffix}",
                "name": f"{station_name} 역무원",
                "email": f"staff.{suffix.lower()}@gyoum.kr",
                "role": Role.STAFF,
                "station_id": station_id,
            }
        )
    return staff


STAFF_SEED = _build_staff_seed()


def _quote_sqlite_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sqlite_station_name_has_unique_index(session: Session) -> bool:
    indexes = session.execute(text("PRAGMA index_list('stations')")).all()
    for index in indexes:
        index_name = index[1]
        is_unique = bool(index[2])
        if not is_unique:
            continue
        columns = session.execute(
            text(f"PRAGMA index_info({_quote_sqlite_identifier(index_name)})")
        ).all()
        if [column[2] for column in columns] == ["name"]:
            return True
    return False


def _allow_duplicate_station_names_for_transfers(session: Session) -> None:
    """SQLite dev DB migration for line-specific transfer station cards."""
    bind = session.get_bind()
    if bind.dialect.name != "sqlite":
        return
    if not _sqlite_station_name_has_unique_index(session):
        return

    session.execute(text("PRAGMA foreign_keys=OFF"))
    session.execute(
        text(
            """
            CREATE TABLE stations_new (
                id VARCHAR(64) NOT NULL,
                name VARCHAR(255) NOT NULL,
                line VARCHAR(128) NOT NULL,
                line_color VARCHAR(16) NOT NULL,
                PRIMARY KEY (id)
            )
            """
        )
    )
    session.execute(
        text(
            """
            INSERT INTO stations_new (id, name, line, line_color)
            SELECT id, name, line, line_color FROM stations
            """
        )
    )
    session.execute(text("DROP TABLE stations"))
    session.execute(text("ALTER TABLE stations_new RENAME TO stations"))
    session.execute(text("CREATE INDEX ix_stations_name ON stations (name)"))
    session.execute(text("PRAGMA foreign_keys=ON"))
    session.commit()


def _line_sort_key(line: str) -> tuple[int, str]:
    order = {
        "1호선": 1,
        "2호선": 2,
        "3호선": 3,
        "4호선": 4,
        "5호선": 5,
        "6호선": 6,
        "7호선": 7,
        "8호선": 8,
        "9호선": 9,
        "경의중앙선": 20,
        "공항철도": 21,
        "경춘선": 22,
        "수인분당선": 23,
        "신분당선": 24,
        "우이신설선": 25,
        "서해선": 26,
        "경강선": 27,
        "신림선": 28,
        "김포골드라인": 29,
        "GTX-A": 30,
    }
    return (order.get(line, 999), line)


def _station_sort_key(station: Station) -> tuple[int, str, tuple[int, str]]:
    name = station.name.strip()
    starts_with_digit = bool(name[:1] and name[0].isdigit())
    return (1 if starts_with_digit else 0, name, _line_sort_key(station.line))

CHECKLIST_TEMPLATES: dict[SupportType, list[tuple[str, str]]] = {
    SupportType.FOOTBOARD: [
        ("prepare-footboard", "이동식 안전발판을 준비했어요."),
        ("confirm-train-gap", "승강장과 열차 사이 발판을 설치했어요."),
    ],
    SupportType.COMPANION: [
        ("meet-passenger", "만남 위치에서 승객을 확인했어요."),
        ("escort-to-platform", "승강장까지 동행 안내를 시작했어요."),
    ],
    SupportType.ELEVATOR: [
        ("guide-elevator-route", "엘리베이터 경유 동선을 안내했어요."),
    ],
    SupportType.VISION: [
        ("verbal-guide", "구두 안내와 인사를 마쳤어요."),
        ("confirm-tactile-blocks", "점자 블록과 동선을 확인했어요."),
    ],
    SupportType.WHEELCHAIR: [
        ("verify-wheelchair-fit", "휠체어 통로와 폭을 확인했어요."),
        ("confirm-clear-path", "이동 동선의 장애물을 확인했어요."),
    ],
    SupportType.CHAT: [
        ("prepare-pen-paper-or-sign", "필담/수어 준비를 마쳤어요."),
    ],
}


@dataclass
class AppService:
    db_factory: Callable[[], Session]

    def seed(self) -> None:
        session = self.db_factory()
        try:
            _allow_duplicate_station_names_for_transfers(session)
            existing_station_ids = set(session.scalars(select(Station.id)))
            missing_stations = [
                Station(id=station_id, name=name, line=line, line_color=line_color)
                for station_id, name, line, line_color in ALL_STATION_SEED
                if station_id not in existing_station_ids
            ]
            if missing_stations:
                session.add_all(missing_stations)
                session.flush()

            station_seed_by_id = {
                station_id: (name, line, line_color)
                for station_id, name, line, line_color in ALL_STATION_SEED
            }
            seeded_stations = session.scalars(
                select(Station).where(Station.id.in_(station_seed_by_id.keys()))
            )
            for station in seeded_stations:
                name, line, line_color = station_seed_by_id[station.id]
                if station.name != name:
                    station.name = name
                if station.line != line:
                    station.line = line
                if station.line_color != line_color:
                    station.line_color = line_color
            if session.scalar(select(User.id).limit(1)) is None:
                session.add_all([User(**item) for item in USER_SEED])
                session.add_all([User(**item) for item in STAFF_SEED])
            else:
                # 기존 DB에 staff가 누락된 역은 자동 추가(다수 역 커버용).
                existing_staff_station_ids = {
                    row
                    for row in session.scalars(
                        select(User.station_id).where(User.role == Role.STAFF)
                    )
                }
                missing_staff = [
                    item
                    for item in STAFF_SEED
                    if item["station_id"] not in existing_staff_station_ids
                ]
                if missing_staff:
                    session.add_all([User(**item) for item in missing_staff])
            session.commit()
        finally:
            session.close()

    def list_stations(self, db: Session, query: str | None) -> list[Station]:
        stmt = select(Station).order_by(Station.name)
        stmt = stmt.where(Station.id.in_(VISIBLE_STATION_IDS))
        if query:
            stmt = stmt.where(Station.name.contains(query))
        return sorted(db.scalars(stmt), key=_station_sort_key)

    def get_demo_user_for_role(
        self,
        db: Session,
        role: Role,
        station_id: str | None = None,
    ) -> User:
        if role == Role.STAFF:
            if not station_id:
                raise HTTPException(status_code=422, detail="근무 역을 선택해주세요.")
            user = db.scalar(
                select(User).where(User.role == Role.STAFF, User.station_id == station_id)
            )
            if not user:
                raise HTTPException(
                    status_code=404,
                    detail=f"해당 역의 역무원 계정을 찾을 수 없습니다: {station_id}",
                )
            return user
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
        stmt = stmt.where(
            SupportRequest.origin_station_id.in_(VISIBLE_STATION_IDS),
            SupportRequest.destination_station_id.in_(VISIBLE_STATION_IDS),
        )
        if actor.role == Role.PASSENGER:
            stmt = stmt.where(SupportRequest.passenger_user_id == actor.id)
        elif actor.role == Role.STAFF:
            stmt = stmt.where(
                (
                    (SupportRequest.origin_station_id == actor.station_id)
                    & SupportRequest.status.in_(
                        [
                            SupportRequestStatus.SUBMITTED,
                            SupportRequestStatus.ASSIGNED,
                            SupportRequestStatus.IN_PROGRESS,
                        ]
                    )
                )
                | (
                    (SupportRequest.assigned_staff_user_id == actor.id)
                    & (SupportRequest.origin_station_id == actor.station_id)
                    & (SupportRequest.status == SupportRequestStatus.BOARDED)
                )
                | (
                    (SupportRequest.destination_station_id == actor.station_id)
                    & SupportRequest.status.in_(
                        [
                            SupportRequestStatus.BOARDED,
                            SupportRequestStatus.AWAITING_DROPOFF,
                            SupportRequestStatus.COMPLETED,
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
        train_number: str | None,
        train_car_number: str | None,
        completion_note: str | None,
    ) -> SupportRequestDetailResponse:
        support_request = self._get_request_entity(db, request_id)
        self._assert_status_actor(actor, support_request)

        normalized_train_number = train_number.strip() if train_number else None
        normalized_train_car_number = train_car_number.strip() if train_car_number else None
        normalized_completion_note = completion_note.strip() if completion_note else None

        if next_status == SupportRequestStatus.BOARDED:
            if not normalized_train_car_number:
                raise HTTPException(status_code=422, detail="Train car number is required")
            if not normalized_train_number:
                raise HTTPException(status_code=422, detail="Train number is required")
        else:
            if normalized_train_car_number is not None:
                raise HTTPException(status_code=422, detail="Train car number is only allowed for boarded status")
            if normalized_train_number is not None:
                raise HTTPException(status_code=422, detail="Train number is only allowed for boarded status")

        if next_status == SupportRequestStatus.COMPLETED:
            if next_status in ALLOWED_TRANSITIONS[support_request.status] and not normalized_completion_note:
                raise HTTPException(status_code=422, detail="Completion note is required")
        elif normalized_completion_note is not None:
            raise HTTPException(status_code=422, detail="Completion note is only allowed for completed status")

        if normalized_train_number:
            support_request.train_number = normalized_train_number
        if normalized_train_car_number:
            support_request.train_car_number = normalized_train_car_number
        if next_status in {
            SupportRequestStatus.AWAITING_DROPOFF,
            SupportRequestStatus.COMPLETED,
        } and actor.station_id == support_request.destination_station_id:
            support_request.assigned_staff_user_id = actor.id
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

    def upload_current_location(
        self,
        db: Session,
        actor: User,
        request_id: str,
        payload: UploadSupportRequestCurrentLocationRequest,
    ) -> SupportRequestDetailResponse:
        support_request = self._get_request_entity(db, request_id)
        if actor.role != Role.PASSENGER or support_request.passenger_user_id != actor.id:
            raise HTTPException(status_code=403, detail="Only the passenger can update current location")
        if support_request.status not in LOCATION_UPLOAD_ALLOWED_STATUSES:
            raise HTTPException(status_code=409, detail="Current location cannot be updated")

        db.add(
            SupportRequestCurrentLocation(
                request_id=support_request.id,
                passenger_user_id=actor.id,
                latitude=payload.latitude,
                longitude=payload.longitude,
                accuracy_meters=payload.accuracy_meters,
            )
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
            actor_role=actor.role,
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
            passenger_name=support_request.passenger.name,
            assigned_staff_name=support_request.assigned_staff.name if support_request.assigned_staff else None,
            assigned_staff_id=support_request.assigned_staff_user_id,
            train_number=support_request.train_number,
            train_car_number=support_request.train_car_number,
            created_at=support_request.created_at,
            boarded_at=self._event_time(support_request, SupportRequestStatus.BOARDED),
            dropoff_started_at=self._event_time(
                support_request,
                SupportRequestStatus.AWAITING_DROPOFF,
            ),
            completed_at=self._event_time(support_request, SupportRequestStatus.COMPLETED),
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
            notes=support_request.notes,
            cancel_reason=self._normalize_cancel_reason(support_request.cancel_reason),
            unavailable_reason=self._normalize_unavailable_reason(
                support_request.unavailable_reason
            ),
            completion_note=support_request.completion_note,
            passenger_id=support_request.passenger_user_id,
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
                    actor_role=event.actor_role or event.actor.role,
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

    # ─── Firebase Push 알림 트리거 ─────────────────────────────────
    def collect_push_tokens(self, db: Session, user_ids: list[str]) -> list[str]:
        """주어진 user_id들에 등록된 모든 push token을 모은다."""
        if not user_ids:
            return []
        tokens = db.scalars(
            select(UserPushToken.token).where(UserPushToken.user_id.in_(user_ids))
        ).all()
        return [token for token in tokens if token]

    def collect_station_staff_user_ids(self, db: Session, station_id: str) -> list[str]:
        """특정 역에 배정된 staff user_id 전부."""
        ids = db.scalars(
            select(User.id).where(User.role == Role.STAFF, User.station_id == station_id)
        ).all()
        return list(ids)

    def _status_message(self, status_value: SupportRequestStatus) -> str:
        messages = {
            SupportRequestStatus.ASSIGNED: "역무원이 배정되었습니다.",
            SupportRequestStatus.IN_PROGRESS: "역무원이 만남 위치로 이동하고 있습니다.",
            SupportRequestStatus.BOARDED: "승차가 완료되었습니다.",
            SupportRequestStatus.AWAITING_DROPOFF: "하차 역에서 지원 처리 중입니다.",
            SupportRequestStatus.COMPLETED: "하차 지원이 완료되었습니다.",
        }
        return messages.get(status_value, status_value.value)

    def _event_time(
        self,
        support_request: SupportRequest,
        status_value: SupportRequestStatus,
    ) -> datetime | None:
        for event in support_request.events:
            if event.to_status == status_value:
                return event.created_at
        return None

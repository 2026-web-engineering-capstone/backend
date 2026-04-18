import sqlite3
from pathlib import Path

import app.dependencies as dependencies
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.enums import MeetingPoint, Role, SupportRequestStatus, SupportType
from app.models import User, UserSession


def sign_in(client, role: str):
    response = client.post("/auth/sign-in", json={"role": role})
    assert response.status_code == 200
    return response


def sign_in_as_user(client, user_id: str):
    client.cookies.clear()
    session = dependencies.database.session_factory()
    try:
        user_session = UserSession(user_id=user_id)
        session.add(user_session)
        session.commit()
        session.refresh(user_session)
        session_id = user_session.id
    finally:
        session.close()

    host = client.base_url.host
    domain = host if "." in host else f"{host}.local"
    client.cookies.set(
        dependencies.settings.session_cookie_name,
        session_id,
        domain=domain,
        path="/",
    )


def create_legacy_support_requests_table(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            CREATE TABLE support_requests (
                id VARCHAR(64) PRIMARY KEY,
                passenger_user_id VARCHAR(64) NOT NULL,
                assigned_staff_user_id VARCHAR(64),
                origin_station_id VARCHAR(64) NOT NULL,
                destination_station_id VARCHAR(64) NOT NULL,
                meeting_point VARCHAR(16) NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                status VARCHAR(32) NOT NULL,
                train_car_number VARCHAR(32),
                cancel_reason VARCHAR(255),
                unavailable_reason VARCHAR(255),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


def create_staff_user(user_id: str, station_id: str):
    session = dependencies.database.session_factory()
    try:
        user = User(
            id=user_id,
            name=f"테스트 역무원 {station_id}",
            email=f"{user_id.lower()}@gyoum.kr",
            role=Role.STAFF,
            station_id=station_id,
        )
        session.add(user)
        session.commit()
    finally:
        session.close()


def test_startup_migrates_existing_sqlite_schema(app_settings):
    database_path = Path(app_settings.database_url.removeprefix("sqlite:///"))
    create_legacy_support_requests_table(database_path)

    from app.main import create_app

    with TestClient(create_app(app_settings)):
        pass

    connection = sqlite3.connect(database_path)
    try:
        columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info('support_requests')")
        }
    finally:
        connection.close()

    assert "completion_note" in columns
    assert columns["completion_note"][3] == 0


def test_create_support_request_succeeds_after_startup_schema_reconcile(app_settings):
    database_path = Path(app_settings.database_url.removeprefix("sqlite:///"))
    create_legacy_support_requests_table(database_path)

    from app.main import create_app

    with TestClient(create_app(app_settings)) as client:
        sign_in(client, "passenger")
        create_response = client.post(
            "/support-requests",
            json={
                "origin_station_id": "STN-ICU",
                "destination_station_id": "STN-CP",
                "meeting_point": MeetingPoint.ELEVATOR,
                "notes": "레거시 스키마 검증",
                "support_types": [SupportType.WHEELCHAIR],
            },
        )

    assert create_response.status_code == 201
    assert create_response.json()["data"]["status"] == SupportRequestStatus.SUBMITTED


def test_startup_schema_reconcile_is_idempotent(app_settings):
    database_path = Path(app_settings.database_url.removeprefix("sqlite:///"))
    create_legacy_support_requests_table(database_path)

    from app.main import create_app

    with TestClient(create_app(app_settings)):
        pass

    with TestClient(create_app(app_settings)):
        pass

    connection = sqlite3.connect(database_path)
    try:
        columns = [
            row[1] for row in connection.execute("PRAGMA table_info('support_requests')")
        ]
    finally:
        connection.close()

    assert columns.count("completion_note") == 1


def test_passenger_can_create_and_list_support_request(client):
    sign_in(client, "passenger")

    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "전동휠체어 사용",
            "support_types": [SupportType.WHEELCHAIR, SupportType.BOARDING_RAMP],
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()["data"]
    assert created["status"] == SupportRequestStatus.SUBMITTED
    assert created["origin_station_name"] == "인천대입구역"
    assert len(created["events"]) == 1

    list_response = client.get("/support-requests")
    assert list_response.status_code == 200
    items = list_response.json()["data"]
    assert len(items) == 1
    assert items[0]["id"] == created["id"]


def test_staff_can_assign_and_progress_support_request(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    client.post("/auth/sign-out")
    sign_in(client, "staff")

    assign_response = client.post(f"/support-requests/{request_id}/assign")
    assert assign_response.status_code == 200
    assert assign_response.json()["data"]["status"] == SupportRequestStatus.ASSIGNED

    progress_response = client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.IN_PROGRESS, "train_car_number": None},
    )
    assert progress_response.status_code == 200
    assert progress_response.json()["data"]["status"] == SupportRequestStatus.IN_PROGRESS

    boarded_response = client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.BOARDED, "train_car_number": "4"},
    )
    assert boarded_response.status_code == 200
    assert boarded_response.json()["data"]["train_car_number"] == "4"


def test_passenger_cannot_assign_support_request(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    assign_response = client.post(f"/support-requests/{request_id}/assign")
    assert assign_response.status_code == 403


def test_unrelated_staff_cannot_assign_support_request(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    client.post("/auth/sign-out")
    create_staff_user("USR-STAFF-OTHER", "STN-GY")
    sign_in_as_user(client, "USR-STAFF-OTHER")

    assign_response = client.post(f"/support-requests/{request_id}/assign")
    assert assign_response.status_code == 403


def test_destination_staff_cannot_assign_support_request_before_handoff(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    client.post("/auth/sign-out")
    create_staff_user("USR-STAFF-DEST-ASSIGN", "STN-CP")
    sign_in_as_user(client, "USR-STAFF-DEST-ASSIGN")

    assign_response = client.post(f"/support-requests/{request_id}/assign")
    assert assign_response.status_code == 403


def test_destination_staff_only_sees_handoff_queue_after_boarded(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    client.post("/auth/sign-out")
    sign_in(client, "staff")
    assign_response = client.post(f"/support-requests/{request_id}/assign")
    assert assign_response.status_code == 200
    assert assign_response.json()["data"]["status"] == SupportRequestStatus.ASSIGNED

    in_progress_response = client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.IN_PROGRESS, "train_car_number": None},
    )
    assert in_progress_response.status_code == 200
    assert in_progress_response.json()["data"]["status"] == SupportRequestStatus.IN_PROGRESS

    create_staff_user("USR-STAFF-DEST", "STN-CP")
    sign_in_as_user(client, "USR-STAFF-DEST")

    list_response = client.get("/support-requests")
    assert list_response.status_code == 200
    items = list_response.json()["data"]
    assert items == []

    client.post("/auth/sign-out")
    sign_in(client, "staff")
    boarded_response = client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.BOARDED, "train_car_number": "4"},
    )
    assert boarded_response.status_code == 200
    assert boarded_response.json()["data"]["status"] == SupportRequestStatus.BOARDED

    client.post("/auth/sign-out")
    sign_in_as_user(client, "USR-STAFF-DEST")

    handoff_response = client.get("/support-requests")
    assert handoff_response.status_code == 200
    handoff_items = handoff_response.json()["data"]
    assert len(handoff_items) == 1
    assert handoff_items[0]["id"] == request_id
    assert handoff_items[0]["status"] == SupportRequestStatus.BOARDED
    assert handoff_items[0]["destination_station_id"] == "STN-CP"


def test_destination_staff_cannot_view_detail_before_boarded(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    client.post("/auth/sign-out")
    sign_in(client, "staff")
    assign_response = client.post(f"/support-requests/{request_id}/assign")
    assert assign_response.status_code == 200

    create_staff_user("USR-STAFF-DEST-DETAIL", "STN-CP")
    sign_in_as_user(client, "USR-STAFF-DEST-DETAIL")

    detail_response = client.get(f"/support-requests/{request_id}")
    assert detail_response.status_code == 403


def test_cannot_skip_directly_to_completed(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    client.post("/auth/sign-out")
    sign_in(client, "staff")
    client.post(f"/support-requests/{request_id}/assign")

    invalid_response = client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.COMPLETED, "train_car_number": None},
    )
    assert invalid_response.status_code == 409


def test_passenger_can_cancel_before_in_progress(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    cancel_response = client.post(
        f"/support-requests/{request_id}/cancel",
        json={"reason": "일정 변경"},
    )
    assert cancel_response.status_code == 200
    cancelled = cancel_response.json()["data"]
    assert cancelled["status"] == SupportRequestStatus.CANCELLED
    assert cancelled["cancel_reason"] == "일정 변경"


def test_passenger_cannot_cancel_after_support_in_progress(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    client.post("/auth/sign-out")
    sign_in(client, "staff")
    client.post(f"/support-requests/{request_id}/assign")
    client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.IN_PROGRESS, "train_car_number": None},
    )

    client.post("/auth/sign-out")
    sign_in(client, "passenger")
    cancel_response = client.post(
        f"/support-requests/{request_id}/cancel",
        json={"reason": "취소 요청"},
    )

    assert cancel_response.status_code == 409


def test_staff_cannot_mark_request_unavailable_before_assignment(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    client.post("/auth/sign-out")
    sign_in(client, "staff")
    unavailable_response = client.post(
        f"/support-requests/{request_id}/unavailable",
        json={"reason": "현장 인력 부족"},
    )

    assert unavailable_response.status_code == 403


def test_create_support_request_auto_generates_checklist_items_from_support_types(client):
    sign_in(client, "passenger")

    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "자동 체크리스트 테스트",
            "support_types": [SupportType.WHEELCHAIR, SupportType.BOARDING_RAMP],
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()["data"]
    checklist_items = created["checklist_items"]
    assert [item["code"] for item in checklist_items] == [
        "prepare-wheelchair-ramp",
        "check-wheelchair-route",
        "prepare-boarding-support",
        "share-boarding-position",
    ]
    assert all(item["checked"] is False for item in checklist_items)
    assert checklist_items[0]["label"] == "휠체어 승하차 발판을 준비했어요."
    assert checklist_items[2]["label"] == "승하차 보조 장비와 위치를 확인했어요."


def test_staff_can_save_and_read_request_checklist(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "체크리스트 저장 테스트",
            "support_types": [SupportType.WHEELCHAIR, SupportType.BOARDING_RAMP],
        },
    )
    request_id = create_response.json()["data"]["id"]

    client.post("/auth/sign-out")
    sign_in(client, "staff")
    assign_response = client.post(f"/support-requests/{request_id}/assign")
    assert assign_response.status_code == 200

    checklist_response = client.post(
        f"/support-requests/{request_id}/checklist",
        json={
            "items": [
                {
                    "code": "prepare-wheelchair-ramp",
                    "label": "휠체어 승하차 발판을 준비했어요.",
                    "checked": True,
                },
                {
                    "code": "check-wheelchair-route",
                    "label": "엘리베이터와 이동 동선을 확인했어요.",
                    "checked": False,
                },
                {
                    "code": "prepare-boarding-support",
                    "label": "승하차 보조 장비와 위치를 확인했어요.",
                    "checked": True,
                },
                {
                    "code": "share-boarding-position",
                    "label": "탑승 위치와 열차 칸 정보를 확인했어요.",
                    "checked": False,
                },
            ]
        },
    )

    assert checklist_response.status_code == 200
    checklist_items = checklist_response.json()["data"]["checklist_items"]
    assert len(checklist_items) == 4
    assert checklist_items[0]["code"] == "prepare-wheelchair-ramp"
    assert checklist_items[0]["checked"] is True
    assert checklist_items[2]["code"] == "prepare-boarding-support"
    assert checklist_items[2]["checked"] is True

    detail_response = client.get(f"/support-requests/{request_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()["data"]
    assert [item["code"] for item in detail["checklist_items"]] == [
        "prepare-wheelchair-ramp",
        "check-wheelchair-route",
        "prepare-boarding-support",
        "share-boarding-position",
    ]
    assert detail["checklist_items"][0]["label"] == "휠체어 승하차 발판을 준비했어요."


def test_passenger_cannot_update_request_checklist(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "권한 테스트",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    checklist_response = client.post(
        f"/support-requests/{request_id}/checklist",
        json={
            "items": [
                {
                    "code": "prepare-wheelchair-ramp",
                    "label": "휠체어 승하차 발판을 준비했어요.",
                    "checked": True,
                }
            ]
        },
    )

    assert checklist_response.status_code == 403


def test_staff_cannot_submit_invalid_request_checklist_items(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "체크리스트 검증 테스트",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    client.post("/auth/sign-out")
    sign_in(client, "staff")
    assign_response = client.post(f"/support-requests/{request_id}/assign")
    assert assign_response.status_code == 200

    invalid_code_response = client.post(
        f"/support-requests/{request_id}/checklist",
        json={
            "items": [
                {
                    "code": "unexpected-item",
                    "label": "임의 항목",
                    "checked": True,
                },
                {
                    "code": "check-wheelchair-route",
                    "label": "엘리베이터와 이동 동선을 확인했어요.",
                    "checked": False,
                },
            ]
        },
    )
    assert invalid_code_response.status_code == 422

    invalid_label_response = client.post(
        f"/support-requests/{request_id}/checklist",
        json={
            "items": [
                {
                    "code": "prepare-wheelchair-ramp",
                    "label": "라벨 변조",
                    "checked": True,
                },
                {
                    "code": "check-wheelchair-route",
                    "label": "엘리베이터와 이동 동선을 확인했어요.",
                    "checked": False,
                },
            ]
        },
    )
    assert invalid_label_response.status_code == 422


def test_assigned_staff_cannot_update_request_checklist_after_completion(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "완료 후 체크리스트 수정 금지 테스트",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    client.post("/auth/sign-out")
    sign_in(client, "staff")
    client.post(f"/support-requests/{request_id}/assign")
    client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.IN_PROGRESS, "train_car_number": None},
    )
    client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.BOARDED, "train_car_number": "4"},
    )

    create_staff_user("USR-STAFF-DEST-CHECKLIST", "STN-CP")
    client.post("/auth/sign-out")
    sign_in_as_user(client, "USR-STAFF-DEST-CHECKLIST")

    client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.AWAITING_DROPOFF, "train_car_number": None},
    )
    completed_response = client.post(
        f"/support-requests/{request_id}/status",
        json={
            "status": SupportRequestStatus.COMPLETED,
            "train_car_number": None,
            "completion_note": "하차 지원을 완료했습니다.",
        },
    )
    assert completed_response.status_code == 200

    client.post("/auth/sign-out")
    sign_in(client, "staff")

    checklist_response = client.post(
        f"/support-requests/{request_id}/checklist",
        json={
            "items": [
                {
                    "code": "prepare-wheelchair-ramp",
                    "label": "휠체어 승하차 발판을 준비했어요.",
                    "checked": True,
                },
                {
                    "code": "check-wheelchair-route",
                    "label": "엘리베이터와 이동 동선을 확인했어요.",
                    "checked": True,
                },
            ]
        },
    )
    assert checklist_response.status_code == 409


def test_staff_must_provide_completion_note_when_completing_request(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    client.post("/auth/sign-out")
    sign_in(client, "staff")
    assign_response = client.post(f"/support-requests/{request_id}/assign")
    assert assign_response.status_code == 200
    in_progress_response = client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.IN_PROGRESS, "train_car_number": None},
    )
    assert in_progress_response.status_code == 200
    boarded_response = client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.BOARDED, "train_car_number": "4"},
    )
    assert boarded_response.status_code == 200
    boarded = boarded_response.json()["data"]

    create_staff_user("USR-STAFF-DEST-COMPLETE", "STN-CP")
    client.post("/auth/sign-out")
    sign_in(client, "staff")

    blocked_handoff_response = client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.AWAITING_DROPOFF, "train_car_number": None},
    )
    assert blocked_handoff_response.status_code == 403

    client.post("/auth/sign-out")
    sign_in_as_user(client, "USR-STAFF-DEST-COMPLETE")

    awaiting_response = client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.AWAITING_DROPOFF, "train_car_number": None},
    )
    assert awaiting_response.status_code == 200
    awaiting = awaiting_response.json()["data"]
    assert awaiting["status"] == SupportRequestStatus.AWAITING_DROPOFF
    assert len(awaiting["events"]) == len(boarded["events"]) + 1
    assert awaiting["events"][-1]["to_status"] == SupportRequestStatus.AWAITING_DROPOFF
    assert awaiting["events"][-1]["actor_name"] == "테스트 역무원 STN-CP"

    client.post("/auth/sign-out")
    sign_in(client, "staff")
    blocked_complete_response = client.post(
        f"/support-requests/{request_id}/status",
        json={
            "status": SupportRequestStatus.COMPLETED,
            "train_car_number": None,
            "completion_note": "원 배정 역무원은 완료할 수 없어야 합니다.",
        },
    )
    assert blocked_complete_response.status_code == 403

    client.post("/auth/sign-out")
    sign_in_as_user(client, "USR-STAFF-DEST-COMPLETE")

    invalid_response = client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.COMPLETED, "train_car_number": None},
    )
    assert invalid_response.status_code == 422

    completed_response = client.post(
        f"/support-requests/{request_id}/status",
        json={
            "status": SupportRequestStatus.COMPLETED,
            "train_car_number": None,
            "completion_note": "하차 지원을 마치고 이동을 도왔습니다.",
        },
    )
    assert completed_response.status_code == 200
    completed = completed_response.json()["data"]
    assert completed["status"] == SupportRequestStatus.COMPLETED
    assert completed["completion_note"] == "하차 지원을 마치고 이동을 도왔습니다."
    assert len(completed["events"]) == len(awaiting["events"]) + 1
    assert completed["events"][-1]["to_status"] == SupportRequestStatus.COMPLETED
    assert completed["events"][-1]["message"] == "하차 지원을 마치고 이동을 도왔습니다."


def test_authenticated_websocket_can_connect(client):
    sign_in(client, "passenger")

    with client.websocket_connect(
        "/support-requests/ws",
        headers={"origin": "http://localhost:8081"},
    ) as websocket:
        assert websocket is not None


def test_websocket_requires_authenticated_session(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/support-requests/ws",
            headers={"origin": "http://localhost:8081"},
        ):
            pass


def test_websocket_requires_allowed_origin(client):
    sign_in(client, "passenger")

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/support-requests/ws",
            headers={"origin": "https://evil.example.com"},
        ):
            pass


def test_websocket_rejects_missing_origin(client):
    sign_in(client, "passenger")

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/support-requests/ws"):
            pass


def test_websocket_allows_regex_origin(client):
    sign_in(client, "passenger")

    with client.websocket_connect(
        "/support-requests/ws",
        headers={"origin": "http://127.0.0.1:19007"},
    ) as websocket:
        assert websocket is not None


def test_support_request_status_change_broadcasts_websocket_event(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "실시간 이벤트 테스트",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    client.post("/auth/sign-out")
    sign_in(client, "staff")

    with client.websocket_connect(
        "/support-requests/ws",
        headers={"origin": "http://localhost:8081"},
    ) as websocket:
        assign_response = client.post(f"/support-requests/{request_id}/assign")
        assert assign_response.status_code == 200

        payload = websocket.receive_json()

    assert payload == {
        "type": "support_request.updated",
        "requestId": request_id,
    }


def test_unrelated_passenger_does_not_receive_other_request_events(client):
    sign_in(client, "passenger")
    passenger_response = client.get("/auth/session")
    passenger_user_id = passenger_response.json()["data"]["user"]["id"]
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "다른 승객 이벤트 차단 테스트",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    session = dependencies.database.session_factory()
    try:
        other_passenger = User(
            id="USR-PASSENGER-OTHER",
            name="다른 승객",
            email="other-passenger@gyoum.kr",
            role=Role.PASSENGER,
            station_id=None,
        )
        session.add(other_passenger)
        session.commit()
    finally:
        session.close()

    client.post("/auth/sign-out")
    sign_in_as_user(client, "USR-PASSENGER-OTHER")

    with client.websocket_connect(
        "/support-requests/ws",
        headers={"origin": "http://localhost:8081"},
    ) as websocket:
        with TestClient(client.app) as actor_client:
            sign_in_as_user(actor_client, "USR-STAFF-DEMO")
            assign_response = actor_client.post(f"/support-requests/{request_id}/assign")
            assert assign_response.status_code == 200

        with pytest.raises(Exception):
            websocket._send_rx.receive_nowait()

    client.post("/auth/sign-out")
    sign_in_as_user(client, passenger_user_id)


def test_destination_staff_does_not_receive_pre_boarded_events(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "하차역 실시간 범위 테스트",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    create_staff_user("USR-STAFF-DEST-WS", "STN-CP")
    client.post("/auth/sign-out")
    sign_in_as_user(client, "USR-STAFF-DEST-WS")

    with client.websocket_connect(
        "/support-requests/ws",
        headers={"origin": "http://localhost:8081"},
    ) as websocket:
        with TestClient(client.app) as actor_client:
            sign_in(actor_client, "staff")
            assign_response = actor_client.post(f"/support-requests/{request_id}/assign")
            assert assign_response.status_code == 200

        with pytest.raises(Exception):
            websocket._send_rx.receive_nowait()


def test_destination_staff_receives_boarded_events(client):
    sign_in(client, "passenger")
    create_response = client.post(
        "/support-requests",
        json={
            "origin_station_id": "STN-ICU",
            "destination_station_id": "STN-CP",
            "meeting_point": MeetingPoint.ELEVATOR,
            "notes": "하차역 보딩 이벤트 테스트",
            "support_types": [SupportType.WHEELCHAIR],
        },
    )
    request_id = create_response.json()["data"]["id"]

    client.post("/auth/sign-out")
    sign_in(client, "staff")
    assign_response = client.post(f"/support-requests/{request_id}/assign")
    assert assign_response.status_code == 200
    progress_response = client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.IN_PROGRESS, "train_car_number": None},
    )
    assert progress_response.status_code == 200

    create_staff_user("USR-STAFF-DEST-WS-BOARDED", "STN-CP")
    client.post("/auth/sign-out")
    sign_in_as_user(client, "USR-STAFF-DEST-WS-BOARDED")

    with client.websocket_connect(
        "/support-requests/ws",
        headers={"origin": "http://localhost:8081"},
    ) as websocket:
        with TestClient(client.app) as actor_client:
            sign_in(actor_client, "staff")
            boarded_response = actor_client.post(
                f"/support-requests/{request_id}/status",
                json={"status": SupportRequestStatus.BOARDED, "train_car_number": "4"},
            )
            assert boarded_response.status_code == 200

        payload = websocket.receive_json()

    assert payload == {
        "type": "support_request.updated",
        "requestId": request_id,
    }

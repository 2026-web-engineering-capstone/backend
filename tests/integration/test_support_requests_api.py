import sqlite3
from pathlib import Path

import app.dependencies as dependencies
from fastapi.testclient import TestClient

from app.enums import MeetingPoint, Role, SupportRequestStatus, SupportType
from app.models import User


def sign_in(client, role: str):
    response = client.post("/auth/sign-in", json={"role": role})
    assert response.status_code == 200
    return response


def sign_in_as_user(client, user_id: str):
    client.cookies.set(dependencies.settings.session_cookie_name, user_id)


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



def test_destination_staff_can_list_and_assign_support_request(client):
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
    create_staff_user("USR-STAFF-DEST", "STN-CP")
    sign_in_as_user(client, "USR-STAFF-DEST")

    list_response = client.get("/support-requests")
    assert list_response.status_code == 200
    items = list_response.json()["data"]
    assert len(items) == 1
    assert items[0]["id"] == request_id

    assign_response = client.post(f"/support-requests/{request_id}/assign")
    assert assign_response.status_code == 200
    assert assign_response.json()["data"]["assigned_staff_id"] == "USR-STAFF-DEST"



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
    client.post(f"/support-requests/{request_id}/assign")
    client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.IN_PROGRESS, "train_car_number": None},
    )
    client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.BOARDED, "train_car_number": "4"},
    )
    client.post(
        f"/support-requests/{request_id}/status",
        json={"status": SupportRequestStatus.AWAITING_DROPOFF, "train_car_number": None},
    )

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

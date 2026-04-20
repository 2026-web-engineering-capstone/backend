from sqlalchemy import select

import app.dependencies as dependencies
from app.models import UserPushToken, UserSession


def test_sign_in_and_session(client):
    sign_in_response = client.post(
        "/auth/sign-in",
        json={"role": "staff", "installation_id": None, "push_token": None, "push_platform": None},
    )

    assert sign_in_response.status_code == 200
    payload = sign_in_response.json()["data"]
    assert payload["user"]["role"] == "staff"

    session_cookie = sign_in_response.cookies.get("gyoum_session")
    assert session_cookie is not None
    assert session_cookie.startswith("SES-")

    session = dependencies.database.session_factory()
    try:
        user_session = session.scalar(
            select(UserSession).where(UserSession.id == session_cookie)
        )
    finally:
        session.close()

    assert user_session is not None
    assert user_session.user_id == "USR-STAFF-DEMO"

    session_response = client.get("/auth/session")
    assert session_response.status_code == 200
    assert session_response.json()["data"]["user"]["email"] == "staff@gyoum.kr"


def test_sign_out_clears_session(client):
    sign_in_response = client.post(
        "/auth/sign-in",
        json={"role": "passenger", "installation_id": None, "push_token": None, "push_platform": None},
    )
    session_cookie = sign_in_response.cookies.get("gyoum_session")

    response = client.post(
        "/auth/sign-out",
        json={"installation_id": "install-1", "push_token": None},
    )

    assert response.status_code == 200

    session = dependencies.database.session_factory()
    try:
        user_session = session.scalar(
            select(UserSession).where(UserSession.id == session_cookie)
        )
    finally:
        session.close()

    assert user_session is None

    session_response = client.get("/auth/session")
    assert session_response.status_code == 401


def test_cors_allows_localhost_dev_origins(client):
    response = client.options(
        "/auth/session",
        headers={
            "Origin": "http://localhost:19007",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:19007"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_allows_loopback_dev_origins(client):
    response = client.options(
        "/auth/session",
        headers={
            "Origin": "http://127.0.0.1:19007",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:19007"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_forged_session_cookie_is_rejected(client):
    client.cookies.set("gyoum_session", "SES-forged-session", domain="testserver.local", path="/")

    session_response = client.get("/auth/session")

    assert session_response.status_code == 401


def test_register_push_token_persists_token_for_current_user(client):
    client.post(
        "/auth/sign-in",
        json={"role": "passenger", "installation_id": None, "push_token": None, "push_platform": None},
    )

    response = client.post(
        "/auth/push-token",
        json={
            "token": "ExponentPushToken[test-token-1]",
            "platform": "android",
            "installation_id": "install-1",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"registered": True}

    session = dependencies.database.session_factory()
    try:
        push_token = session.scalar(
            select(UserPushToken).where(
                UserPushToken.token == "ExponentPushToken[test-token-1]"
            )
        )
    finally:
        session.close()

    assert push_token is not None
    assert push_token.user_id == "USR-PASSENGER-DEMO"
    assert push_token.platform == "android"
    assert push_token.installation_id == "install-1"


def test_register_push_token_is_idempotent(client):
    client.post(
        "/auth/sign-in",
        json={"role": "passenger", "installation_id": None, "push_token": None, "push_platform": None},
    )

    first_response = client.post(
        "/auth/push-token",
        json={
            "token": "ExponentPushToken[test-token-1]",
            "platform": "android",
            "installation_id": "install-1",
        },
    )
    second_response = client.post(
        "/auth/push-token",
        json={
            "token": "ExponentPushToken[test-token-1]",
            "platform": "android",
            "installation_id": "install-1",
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    session = dependencies.database.session_factory()
    try:
        push_tokens = list(
            session.scalars(
                select(UserPushToken).where(
                    UserPushToken.user_id == "USR-PASSENGER-DEMO"
                )
            )
        )
    finally:
        session.close()

    assert len(push_tokens) == 1


def test_register_push_token_allows_account_switch_on_same_installation(client):
    client.post(
        "/auth/sign-in",
        json={"role": "passenger", "installation_id": None, "push_token": None, "push_platform": None},
    )
    client.post(
        "/auth/push-token",
        json={
            "token": "ExponentPushToken[test-token-1]",
            "platform": "android",
            "installation_id": "install-1",
        },
    )
    client.post(
        "/auth/sign-in",
        json={"role": "staff", "installation_id": None, "push_token": None, "push_platform": None},
    )

    response = client.post(
        "/auth/push-token",
        json={
            "token": "ExponentPushToken[test-token-1]",
            "platform": "android",
            "installation_id": "install-1",
        },
    )

    assert response.status_code == 200

    session = dependencies.database.session_factory()
    try:
        push_token = session.scalar(
            select(UserPushToken).where(
                UserPushToken.installation_id == "install-1"
            )
        )
    finally:
        session.close()

    assert push_token is not None
    assert push_token.user_id == "USR-STAFF-DEMO"
    assert push_token.token == "ExponentPushToken[test-token-1]"


def test_register_push_token_rejects_reassignment_from_other_installation(client):
    client.post(
        "/auth/sign-in",
        json={"role": "passenger", "installation_id": None, "push_token": None, "push_platform": None},
    )
    client.post(
        "/auth/push-token",
        json={
            "token": "ExponentPushToken[test-token-1]",
            "platform": "android",
            "installation_id": "install-1",
        },
    )
    client.post(
        "/auth/sign-in",
        json={"role": "staff", "installation_id": None, "push_token": None, "push_platform": None},
    )

    response = client.post(
        "/auth/push-token",
        json={
            "token": "ExponentPushToken[test-token-1]",
            "platform": "android",
            "installation_id": "install-2",
        },
    )

    assert response.status_code == 409

    session = dependencies.database.session_factory()
    try:
        push_token = session.scalar(
            select(UserPushToken).where(
                UserPushToken.token == "ExponentPushToken[test-token-1]"
            )
        )
    finally:
        session.close()

    assert push_token is not None
    assert push_token.user_id == "USR-PASSENGER-DEMO"
    assert push_token.installation_id == "install-1"


def test_unregister_push_token_removes_only_current_installation_token(client):
    client.post(
        "/auth/sign-in",
        json={"role": "passenger", "installation_id": None, "push_token": None, "push_platform": None},
    )
    client.post(
        "/auth/push-token",
        json={
            "token": "ExponentPushToken[test-token-1]",
            "platform": "android",
            "installation_id": "install-1",
        },
    )
    client.post(
        "/auth/push-token",
        json={
            "token": "ExponentPushToken[test-token-2]",
            "platform": "android",
            "installation_id": "install-2",
        },
    )

    response = client.request(
        "DELETE",
        "/auth/push-token",
        json={"installation_id": "install-1"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"unregistered": True}

    session = dependencies.database.session_factory()
    try:
        push_tokens = list(
            session.scalars(
                select(UserPushToken).where(
                    UserPushToken.user_id == "USR-PASSENGER-DEMO"
                )
            )
        )
    finally:
        session.close()

    assert [item.installation_id for item in push_tokens] == ["install-2"]
    assert [item.token for item in push_tokens] == ["ExponentPushToken[test-token-2]"]


def test_sign_in_reassigns_installation_when_push_payload_is_provided(client):
    client.post(
        "/auth/sign-in",
        json={"role": "passenger", "installation_id": None, "push_token": None, "push_platform": None},
    )
    client.post(
        "/auth/push-token",
        json={
            "token": "ExponentPushToken[test-token-1]",
            "platform": "android",
            "installation_id": "install-1",
        },
    )

    response = client.post(
        "/auth/sign-in",
        json={
            "role": "staff",
            "installation_id": "install-1",
            "push_token": "ExponentPushToken[test-token-1]",
            "push_platform": "android",
        },
    )

    assert response.status_code == 200

    session = dependencies.database.session_factory()
    try:
        push_token = session.scalar(
            select(UserPushToken).where(UserPushToken.installation_id == "install-1")
        )
    finally:
        session.close()

    assert push_token is not None
    assert push_token.user_id == "USR-STAFF-DEMO"


def test_sign_out_requires_matching_token_to_unregister_installation(client):
    client.post(
        "/auth/sign-in",
        json={"role": "passenger", "installation_id": None, "push_token": None, "push_platform": None},
    )
    client.post(
        "/auth/push-token",
        json={
            "token": "ExponentPushToken[test-token-1]",
            "platform": "android",
            "installation_id": "install-1",
        },
    )

    response = client.post(
        "/auth/sign-out",
        json={"installation_id": "install-1", "push_token": "wrong-token"},
    )

    assert response.status_code == 200

    session = dependencies.database.session_factory()
    try:
        push_token = session.scalar(
            select(UserPushToken).where(UserPushToken.installation_id == "install-1")
        )
    finally:
        session.close()

    assert push_token is not None
    assert push_token.token == "ExponentPushToken[test-token-1]"


def test_sign_out_removes_token_by_installation_and_token_after_session_loss(client):
    sign_in_response = client.post(
        "/auth/sign-in",
        json={"role": "passenger", "installation_id": None, "push_token": None, "push_platform": None},
    )
    client.post(
        "/auth/push-token",
        json={
            "token": "ExponentPushToken[test-token-1]",
            "platform": "android",
            "installation_id": "install-1",
        },
    )

    session_cookie = sign_in_response.cookies.get("gyoum_session")
    session = dependencies.database.session_factory()
    try:
        dependencies.delete_user_session(session, session_cookie)
    finally:
        session.close()

    response = client.post(
        "/auth/sign-out",
        json={
            "installation_id": "install-1",
            "push_token": "ExponentPushToken[test-token-1]",
        },
    )

    assert response.status_code == 200

    session = dependencies.database.session_factory()
    try:
        push_token = session.scalar(
            select(UserPushToken).where(UserPushToken.installation_id == "install-1")
        )
    finally:
        session.close()

    assert push_token is None


def test_register_push_token_requires_authentication(client):
    response = client.post(
        "/auth/push-token",
        json={
            "token": "ExponentPushToken[test-token-1]",
            "platform": "android",
            "installation_id": "install-1",
        },
    )

    assert response.status_code == 401

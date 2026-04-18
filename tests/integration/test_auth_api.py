from sqlalchemy import select

import app.dependencies as dependencies
from app.models import UserSession


def test_sign_in_and_session(client):
    sign_in_response = client.post("/auth/sign-in", json={"role": "staff"})

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
    sign_in_response = client.post("/auth/sign-in", json={"role": "passenger"})
    session_cookie = sign_in_response.cookies.get("gyoum_session")

    response = client.post("/auth/sign-out")

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

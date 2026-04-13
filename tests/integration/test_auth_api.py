def test_sign_in_and_session(client):
    sign_in_response = client.post("/auth/sign-in", json={"role": "staff"})

    assert sign_in_response.status_code == 200
    payload = sign_in_response.json()["data"]
    assert payload["user"]["role"] == "staff"

    session_response = client.get("/auth/session")
    assert session_response.status_code == 200
    assert session_response.json()["data"]["user"]["email"] == "staff@gyoum.kr"


def test_sign_out_clears_session(client):
    client.post("/auth/sign-in", json={"role": "passenger"})
    response = client.post("/auth/sign-out")

    assert response.status_code == 200

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

def test_list_stations(client):
    response = client.get("/stations")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) >= 11
    assert any(item["name"] == "인천대입구역" for item in data)


def test_filter_stations(client):
    response = client.get("/stations", params={"query": "센트럴"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["name"] == "센트럴파크역"

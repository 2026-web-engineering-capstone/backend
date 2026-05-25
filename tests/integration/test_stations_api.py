def test_list_stations(client):
    response = client.get("/stations")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) >= 5
    assert any(item["name"] == "강남역" for item in data)


def test_filter_stations(client):
    response = client.get("/stations", params={"query": "역삼"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["name"] == "역삼역"

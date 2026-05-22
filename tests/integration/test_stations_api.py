def test_list_stations(client):
    response = client.get("/stations")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) >= 11
    assert any(item["name"] == "한성대입구역" for item in data)


def test_filter_stations(client):
    response = client.get("/stations", params={"query": "혜화"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["name"] == "혜화역"


def test_transfer_station_lines_are_listed_as_separate_cards(client):
    response = client.get("/stations", params={"query": "동대문역사문화공원"})

    assert response.status_code == 200
    data = response.json()["data"]
    lines = [item["line"] for item in data]
    ids = [item["id"] for item in data]

    assert lines == ["2호선", "4호선", "5호선"]
    assert len(ids) == len(set(ids))


def test_station_list_sorts_digit_names_after_text_names(client):
    response = client.get("/stations")

    assert response.status_code == 200
    data = response.json()["data"]
    first_digit_index = next(
        index for index, item in enumerate(data) if item["name"][0].isdigit()
    )

    assert any(item["name"] == "4.19 민주묘지역" for item in data)
    assert all(item["name"][0].isdigit() for item in data[first_digit_index:])

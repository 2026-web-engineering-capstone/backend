from app.services.transit_service import _parse_seoul_arrival_train


def test_parse_seoul_arrival_train_maps_subway_id_to_line():
    train = _parse_seoul_arrival_train(
        {
            "subwayId": "1002",
            "trainLineNm": "성수행 - 왕십리방면 (급행)",
            "bstatnNm": "성수",
            "arvlMsg2": "전역 도착",
            "arvlMsg3": "뚝섬",
            "updnLine": "내선",
            "btrainNo": "2201",
            "barvlDt": "240",
        }
    )

    assert train.line == "2호선"
    assert train.line_id == "1002"
    assert train.destination == "성수"
    assert train.destination_label == "성수행"
    assert train.route_label == "왕십리방면"
    assert train.train_status == "급행"
    assert train.current_station == "뚝섬"
    assert train.eta_message == "전역 도착"
    assert train.eta_seconds == 240


def test_parse_seoul_arrival_train_keeps_unknown_line_name_fallback():
    train = _parse_seoul_arrival_train(
        {
            "subwayId": "9999",
            "subwayNm": "테스트선",
            "trainLineNm": "테스트행",
            "arvlMsg3": "도착",
        }
    )

    assert train.line == "테스트선"
    assert train.line_id == "9999"
    assert train.destination_label == "테스트행"

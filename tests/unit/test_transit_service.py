from app.services.transit_service import (
    _build_api_facilities,
    _build_official_csv_facilities,
    _build_seoul_accessible_facilities,
    _build_seoul_metro_csv_facilities,
    _merge_facility_payloads,
    _normalize_seoul_metro_csv_line,
    _parse_seoul_arrival_train,
)


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


def test_api_facilities_include_weak_person_slope_and_counts():
    payload = _build_api_facilities(
        "서울",
        [
            {
                "pwdbs_slwy_estnc": "Y",
                "pwdbs_tolt_estnc": "Y",
                "whlch_liftt_cnt": 2,
            }
        ],
        [{"elevt_cnt": 18, "esclt_cnt": 23, "nrsrm_estnc": "Y"}],
    )

    assert [(item.facility_type, item.location_note) for item in payload.facilities] == [
        ("엘리베이터", "18대"),
        ("장애인 경사로", "설치됨"),
        ("장애인 화장실", "설치됨"),
        ("휠체어 리프트", "2대"),
    ]


def test_seoul_metro_csv_facilities_fill_non_korail_stations():
    payload = _build_seoul_metro_csv_facilities("영등포구청")

    assert [(item.facility_type, item.location_note) for item in payload.facilities] == [
        ("엘리베이터", "설치됨"),
    ]


def test_official_csv_facilities_fill_korail_outer_station():
    payload = _build_official_csv_facilities("가능")

    assert [(item.facility_type, item.location_note) for item in payload.facilities] == [
        ("엘리베이터", "2대"),
        ("에스컬레이터", "4대"),
    ]


def test_official_csv_facilities_fill_airport_railroad_restrooms():
    payload = _build_official_csv_facilities("계양")

    assert payload.facilities[0].facility_type == "장애인 화장실"
    assert "역무실 우측" in (payload.facilities[0].location_note or "")
    assert "공항철도" not in (payload.facilities[0].location_note or "")


def test_seoul_metro_csv_line_normalization_removes_operator_prefix():
    assert _normalize_seoul_metro_csv_line("05호선") == "5호선"
    assert _normalize_seoul_metro_csv_line("25호선") == "5호선"
    assert _normalize_seoul_metro_csv_line("28호선") == "8호선"


def test_seoul_accessible_api_facilities_filter_station_and_summarize():
    payload = _build_seoul_accessible_facilities(
        "영등포구청",
        {
            "엘리베이터": [
                {
                    "stnNm": "영등포구청",
                    "lineNm": "5호선",
                    "dtlPstn": "2번 출입구",
                },
                {
                    "stnNm": "영등포구청",
                    "lineNm": "2호선",
                    "dtlPstn": "5번 출입구",
                },
                {
                    "stnNm": "홍대입구",
                    "lineNm": "2호선",
                    "dtlPstn": "8번 출입구",
                },
            ],
            "장애인 화장실": [
                {
                    "stnNm": "영등포구청",
                    "lineNm": "5호선",
                    "stnFlr": "B1",
                },
            ],
            "교통약자 도우미": [
                {
                    "stnNm": "영등포구청",
                    "lineNm": "5호선",
                    "trffcWksnHlprTelno": "02-123-4567",
                },
            ],
        },
    )

    assert [(item.facility_type, item.location_note) for item in payload.facilities] == [
        ("엘리베이터", "2대 · 2번 출입구, 5번 출입구"),
        ("장애인 화장실", "1개소 · B1"),
        ("교통약자 도우미", "운영 · 02-123-4567"),
    ]


def test_facility_merge_prefers_first_source_per_facility_type():
    seoul_payload = _build_seoul_accessible_facilities(
        "서울",
        {"엘리베이터": [{"stnNm": "서울", "lineNm": "1호선", "dtlPstn": "1번 출입구"}]},
    )
    korail_payload = _build_api_facilities(
        "서울",
        [{"pwdbs_slwy_estnc": "Y", "pwdbs_tolt_estnc": "Y", "whlch_liftt_cnt": 1}],
        [{"elevt_cnt": 18}],
    )

    merged = _merge_facility_payloads("서울", seoul_payload, korail_payload)

    assert [(item.facility_type, item.location_note) for item in merged.facilities] == [
        ("엘리베이터", "1대 · 1번 출입구"),
        ("장애인 경사로", "설치됨"),
        ("장애인 화장실", "설치됨"),
        ("휠체어 리프트", "1대"),
    ]

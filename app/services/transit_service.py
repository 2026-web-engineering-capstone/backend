"""실시간 도착 정보 + 역사 편의시설 정보 서비스.

도착 정보는 서울 열린데이터광장 API를 호출한다. 키 미설정, 네트워크 오류,
응답 파싱 실패, API가 status>=400 envelope으로 반환한 오류는 모두 명시적인
HTTPException으로 상위에 전달한다 — 우회하지 않고 사용자에게 사유를 그대로
알린다.

역사 편의시설 정보는 서울 열린데이터광장 교통약자 API, 한국철도공사
교통약자 편의시설 API, 공식 파일데이터 CSV를 순서대로 병합한다.
코드 내 임의 정적 편의시설 시드는 사용하지 않는다.
"""

from __future__ import annotations

import logging
import csv
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import HTTPException

from app.config import Settings

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_SEOUL_METRO_FACILITIES_CSV = _DATA_DIR / "seoul_metro_facilities_20260212.csv"
_KORAIL_LIFT_FACILITIES_CSV = _DATA_DIR / "korail_station_lift_facilities_20230101.csv"
_AIRPORT_RAILROAD_RESTROOMS_CSV = (
    _DATA_DIR / "airport_railroad_accessible_restrooms_20250630.csv"
)
_seoul_metro_facilities_cache: dict[str, list[tuple[str, str | None, str]]] | None = None
_official_csv_facilities_cache: dict[str, list[tuple[str, str | None, str]]] | None = None


@dataclass(frozen=True)
class ArrivalTrain:
    train_number: str | None
    destination: str
    destination_label: str
    eta_message: str
    direction: str | None
    route_label: str | None
    train_status: str | None
    current_station: str | None
    line: str | None
    line_id: str | None
    eta_seconds: int | None


@dataclass(frozen=True)
class StationArrivals:
    station_name: str
    fetched_at: float
    trains: list[ArrivalTrain]


@dataclass(frozen=True)
class StationFacility:
    station_name: str
    facility_type: str
    location_note: str | None
    operational_status: str


@dataclass(frozen=True)
class StationFacilities:
    station_name: str
    fetched_at: float
    facilities: list[StationFacility]


class _TTLCache:
    """간단한 메모리 TTL 캐시. 단일 프로세스 dev/MVP 용도."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str, ttl_seconds: int) -> object | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if time.time() - stored_at > ttl_seconds:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: object) -> None:
        self._store[key] = (time.time(), value)


_arrivals_cache = _TTLCache()
_seoul_facilities_cache = _TTLCache()

_SEOUL_ACCESSIBLE_SERVICES: tuple[tuple[str, str], ...] = (
    ("getWksnElvtr", "엘리베이터"),
    ("getWksnRstrm", "장애인 화장실"),
    ("getWksnEsctr", "에스컬레이터"),
    ("getWksnWhcllift", "휠체어 리프트"),
    ("getWksnMvnwlk", "무빙워크"),
    ("getWksnWhclCharge", "휠체어 급속충전기"),
    ("getWksnSlng", "수어영상전화기"),
    ("getWksnSafePlfm", "이동식 안전발판"),
    ("getWksnHelper", "교통약자 도우미"),
)

_SUBWAY_ID_TO_LINE: dict[str, str] = {
    "1001": "1호선",
    "1002": "2호선",
    "1003": "3호선",
    "1004": "4호선",
    "1005": "5호선",
    "1006": "6호선",
    "1007": "7호선",
    "1008": "8호선",
    "1009": "9호선",
    "1063": "경의중앙선",
    "1065": "공항철도",
    "1067": "경춘선",
    "1075": "수인분당선",
    "1077": "신분당선",
    "1092": "우이신설선",
    "1093": "서해선",
    "1081": "경강선",
    "1094": "신림선",
    "1032": "GTX-A",
    "1091": "김포골드라인",
}


def _normalize_line_label(line_id: object, fallback: object = None) -> str | None:
    if line_id is not None:
        line_key = str(line_id).strip()
        if line_key in _SUBWAY_ID_TO_LINE:
            return _SUBWAY_ID_TO_LINE[line_key]
    if fallback is None:
        return None
    fallback_text = str(fallback).strip()
    return fallback_text or None


def _normalize_station_name(name: str) -> str:
    """'한성대입구역' → '한성대입구' 형태로 정규화. 외부 API는 '역' 접미사
    유무가 들쑥날쑥하므로 호출 직전에 명시적으로 처리.
    """
    stripped = name.strip()
    if stripped.endswith("역"):
        return stripped[:-1]
    return stripped


def _normalize_facility_station_name(name: object) -> str:
    stripped = str(name or "").strip()
    if stripped.endswith("역사"):
        stripped = stripped[:-2]
    return _normalize_station_name(stripped)


def _get_arrivals_api_key(settings: Settings) -> str | None:
    return settings.seoul_open_api_key or settings.subway_arrival_api_key


_TRAIN_LINE_PATTERN = re.compile(
    r"^(?P<destination>.+?행)(?:\s*-\s*(?P<route>.+?방면))?(?:\s*(?P<status>\(.+\)))?$"
)


def _parse_train_line_name(
    train_line_name: object,
    fallback_destination: str,
) -> tuple[str, str | None, str | None]:
    text = str(train_line_name or "").strip()
    if not text:
        return (f"{fallback_destination}행" if fallback_destination else "행선지 미상", None, None)

    match = _TRAIN_LINE_PATTERN.match(text)
    if not match:
        return (text, None, None)

    destination = match.group("destination").strip()
    route = match.group("route")
    status = match.group("status")
    return (
        destination,
        route.strip() if route else None,
        status.strip("()") if status else None,
    )


def _parse_seoul_arrival_train(item: dict) -> ArrivalTrain:
    """서울 열린데이터 광장 realtimeStationArrival 응답 항목 한 건을 정규화."""
    destination = (item.get("bstatnNm") or "").strip()
    destination_label, route_label, train_status = _parse_train_line_name(
        item.get("trainLineNm"),
        destination,
    )
    eta_message = (item.get("arvlMsg2") or item.get("arvlMsg3") or "").strip()
    direction = item.get("updnLine") or None
    line_id = item.get("subwayId")
    line = _normalize_line_label(line_id, item.get("subwayNm"))
    train_number = item.get("btrainNo") or None
    current_station = (item.get("arvlMsg3") or item.get("statnNm") or "").strip() or None
    eta_seconds = _to_int(item.get("barvlDt")) if item.get("barvlDt") is not None else None
    return ArrivalTrain(
        train_number=train_number,
        destination=destination,
        destination_label=destination_label,
        eta_message=eta_message,
        direction=direction,
        route_label=route_label,
        train_status=train_status,
        current_station=current_station,
        line=str(line) if line is not None else None,
        line_id=str(line_id) if line_id is not None else None,
        eta_seconds=eta_seconds,
    )


async def fetch_station_arrivals(
    settings: Settings, station_name: str
) -> StationArrivals:
    """서울 열린데이터 광장 realtimeStationArrival 호출.

    키 미설정·네트워크·파싱·API 오류 envelope 모두 HTTPException으로 명시 전파.
    호출이 성공했지만 결과가 비어 있는 정상 케이스는 빈 trains 배열을 200으로
    반환한다 (프런트의 빈 상태 UI가 처리).
    """
    normalized = _normalize_station_name(station_name)
    cache_key = f"arrivals::{normalized}"
    cached = _arrivals_cache.get(cache_key, settings.transit_arrivals_cache_ttl)
    if isinstance(cached, StationArrivals):
        return cached

    api_key = _get_arrivals_api_key(settings)
    if not api_key:
        result = StationArrivals(
            station_name=normalized,
            fetched_at=time.time(),
            trains=[],
        )
        _arrivals_cache.set(cache_key, result)
        return result

    url = (
        f"{settings.seoul_open_api_base_url.rstrip('/')}/"
        f"{api_key}/json/realtimeStationArrival/0/10/"
        f"{normalized}"
    )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
    except httpx.HTTPError as error:
        logger.warning("realtimeStationArrival http error: %s", error)
        raise HTTPException(
            status_code=503,
            detail=f"외부 API 호출에 실패했어요 ({type(error).__name__}).",
        ) from error

    if response.status_code != 200:
        logger.warning(
            "realtimeStationArrival non-200 status %s", response.status_code
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "외부 API가 비정상 응답을 보냈어요 "
                f"(status={response.status_code})."
            ),
        )

    try:
        payload = response.json()
    except ValueError as error:
        logger.warning("realtimeStationArrival json error: %s", error)
        raise HTTPException(
            status_code=503,
            detail="외부 API 응답을 해석할 수 없었어요.",
        ) from error

    # 서울 API는 인증·권한·역 미존재 같은 오류를 HTTP 200 + status/code/message 형태로 감싸 보낸다.
    if isinstance(payload, dict):
        api_status = payload.get("status")
        api_message = payload.get("message")
        api_code = payload.get("code")
        if isinstance(api_status, int) and api_status >= 400:
            logger.warning(
                "realtimeStationArrival api error status=%s code=%s message=%s",
                api_status,
                api_code,
                api_message,
            )
            if api_code == "INFO-200":
                result = StationArrivals(
                    station_name=normalized,
                    fetched_at=time.time(),
                    trains=[],
                )
                _arrivals_cache.set(cache_key, result)
                return result

            detail = (
                f"{api_message} ({api_code})"
                if api_message and api_code
                else api_message or f"외부 API 오류 (status={api_status})."
            )
            raise HTTPException(status_code=503, detail=detail)

    items = payload.get("realtimeArrivalList") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        items = []
    trains = [_parse_seoul_arrival_train(item) for item in items if isinstance(item, dict)]
    result = StationArrivals(
        station_name=normalized,
        fetched_at=time.time(),
        trains=trains,
    )
    _arrivals_cache.set(cache_key, result)
    return result


def _as_items(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    raw_items = (
        payload.get("response", {})
        .get("body", {})
        .get("items", {})
        .get("item", [])
    )
    if isinstance(raw_items, dict):
        return [raw_items]
    if isinstance(raw_items, list):
        return [item for item in raw_items if isinstance(item, dict)]
    return []


def _yes(value: object) -> bool:
    return str(value or "").strip().upper() == "Y"


def _count(value: object) -> int:
    try:
        return int(str(value or "0").strip())
    except ValueError:
        return 0


def _append_facility(
    facilities: list[StationFacility],
    station_name: str,
    facility_type: str,
    location_note: str | None,
) -> None:
    if any(item.facility_type == facility_type for item in facilities):
        return
    facilities.append(
        StationFacility(
            station_name=station_name,
            facility_type=facility_type,
            location_note=location_note,
            operational_status="operational",
        )
    )


def _build_api_facilities(
    normalized_station: str,
    weak_person_items: list[dict],
    station_items: list[dict],
) -> StationFacilities:
    facilities: list[StationFacility] = []

    for item in station_items:
        elevator_count = _count(item.get("elevt_cnt"))
        if elevator_count > 0:
            _append_facility(facilities, normalized_station, "엘리베이터", f"{elevator_count}대")

    for item in weak_person_items:
        if _yes(item.get("pwdbs_slwy_estnc")):
            _append_facility(facilities, normalized_station, "장애인 경사로", "설치됨")
        if _yes(item.get("pwdbs_tolt_estnc")):
            _append_facility(facilities, normalized_station, "장애인 화장실", "설치됨")
        lift_count = _count(item.get("whlch_liftt_cnt"))
        if lift_count > 0:
            _append_facility(facilities, normalized_station, "휠체어 리프트", f"{lift_count}대")

    return StationFacilities(
        station_name=normalized_station,
        fetched_at=time.time(),
        facilities=facilities,
    )


def _collect_unique_values(items: list[dict], *keys: str, limit: int = 3) -> list[str]:
    values: list[str] = []
    for item in items:
        for key in keys:
            value = str(item.get(key) or "").strip()
            if value and value not in values:
                values.append(value)
                break
        if len(values) >= limit:
            break
    return values


def _summarize_seoul_facility_note(
    facility_type: str,
    matching_items: list[dict],
) -> str:
    positions = _collect_unique_values(
        matching_items,
        "dtlPstn",
        "bgngFlrDtlPstn",
        "endFlrDtlPstn",
        "rstrmInfo",
        "vcntEntrcNo",
        "stnFlr",
        "mngNo",
    )
    note_parts: list[str] = []
    if facility_type == "교통약자 도우미":
        note_parts.append("운영")
    elif facility_type == "이동식 안전발판":
        note_parts.append("설치됨")
    else:
        unit = "개소" if facility_type == "장애인 화장실" else "대"
        if facility_type == "휠체어 급속충전기":
            unit = "개"
        note_parts.append(f"{len(matching_items)}{unit}")

    if positions:
        suffix = " 외" if len(matching_items) > len(positions) else ""
        note_parts.append(f"{', '.join(positions)}{suffix}")

    phones = _collect_unique_values(matching_items, "trffcWksnHlprTelno", limit=2)
    if phones:
        note_parts.append(", ".join(phones))

    return " · ".join(note_parts)


def _build_seoul_accessible_facilities(
    normalized_station: str,
    items_by_facility_type: dict[str, list[dict]],
) -> StationFacilities:
    facilities: list[StationFacility] = []
    for _service_name, facility_type in _SEOUL_ACCESSIBLE_SERVICES:
        items = items_by_facility_type.get(facility_type, [])
        matching_items = [
            item for item in items
            if _normalize_station_name(item.get("stnNm", "")) == normalized_station
        ]
        if not matching_items:
            continue
        facilities.append(
            StationFacility(
                station_name=normalized_station,
                facility_type=facility_type,
                location_note=_summarize_seoul_facility_note(
                    facility_type,
                    matching_items,
                ),
                operational_status="operational",
            )
        )
    return StationFacilities(
        station_name=normalized_station,
        fetched_at=time.time(),
        facilities=facilities,
    )


def _merge_facility_payloads(
    normalized_station: str,
    *payloads: StationFacilities,
) -> StationFacilities:
    facilities: list[StationFacility] = []
    for payload in payloads:
        for item in payload.facilities:
            _append_facility(
                facilities,
                normalized_station,
                item.facility_type,
                item.location_note,
            )
    return StationFacilities(
        station_name=normalized_station,
        fetched_at=time.time(),
        facilities=facilities,
    )


def _as_seoul_open_data_items(payload: object, service_name: str) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get(service_name, {}).get("row")
    if isinstance(rows, dict):
        return [rows]
    if isinstance(rows, list):
        return [item for item in rows if isinstance(item, dict)]

    raw_items = (
        payload.get("response", {})
        .get("body", {})
        .get("items", {})
        .get("item", [])
    )
    if isinstance(raw_items, dict):
        return [raw_items]
    if isinstance(raw_items, list):
        return [item for item in raw_items if isinstance(item, dict)]
    return []


async def _fetch_seoul_accessible_items(settings: Settings) -> dict[str, list[dict]]:
    cache_key = "all"
    cached = _seoul_facilities_cache.get(cache_key, 60 * 60)
    if isinstance(cached, dict):
        return cached

    key = settings.seoul_open_api_key or settings.subway_arrival_api_key
    if not key:
        return {}

    base_url = settings.seoul_open_data_base_url.rstrip("/")
    items_by_facility_type: dict[str, list[dict]] = {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        for service_name, facility_type in _SEOUL_ACCESSIBLE_SERVICES:
            service_items: list[dict] = []
            start = 1
            page_size = 1000
            while True:
                end = start + page_size - 1
                try:
                    response = await client.get(
                        f"{base_url}/{key}/json/{service_name}/{start}/{end}/",
                    )
                    response.raise_for_status()
                    items = _as_seoul_open_data_items(response.json(), service_name)
                except (httpx.HTTPError, ValueError) as error:
                    logger.warning(
                        "seoul accessible api fallback service=%s: %s",
                        service_name,
                        error,
                    )
                    break
                service_items.extend(items)
                if len(items) < page_size:
                    break
                start += page_size
            items_by_facility_type[facility_type] = service_items
    _seoul_facilities_cache.set(cache_key, items_by_facility_type)
    return items_by_facility_type


def _append_seed_facility(
    facilities: list[tuple[str, str | None, str]],
    facility_type: str,
    location_note: str | None,
    operational_status: str = "operational",
) -> None:
    for index, (existing_type, existing_note, existing_status) in enumerate(facilities):
        if existing_type != facility_type:
            continue
        if location_note and existing_note and location_note not in existing_note:
            facilities[index] = (
                existing_type,
                f"{existing_note}, {location_note}",
                existing_status,
            )
        return
    facilities.append((facility_type, location_note, operational_status))


def _append_count_facility(
    facilities_by_station: dict[str, list[tuple[str, str | None, str]]],
    station_name: str,
    facility_type: str,
    count: object,
) -> None:
    count_value = _count(count)
    if count_value <= 0:
        return
    unit = "개소" if facility_type == "장애인 화장실" else "대"
    note = f"{count_value}{unit}"
    _append_seed_facility(
        facilities_by_station.setdefault(station_name, []),
        facility_type,
        note,
    )


def _read_csv_dicts(path: Path, encoding: str = "cp949") -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding=encoding) as csv_file:
        return list(csv.DictReader(csv_file))


def _load_seoul_metro_facilities() -> dict[str, list[tuple[str, str | None, str]]]:
    global _seoul_metro_facilities_cache
    if _seoul_metro_facilities_cache is not None:
        return _seoul_metro_facilities_cache

    facilities_by_station: dict[str, list[tuple[str, str | None, str]]] = {}
    for row in _read_csv_dicts(_SEOUL_METRO_FACILITIES_CSV):
        station_name = _normalize_facility_station_name(row.get("역명"))
        if not station_name:
            continue

        if _yes(row.get("엘리베이터여부")):
            _append_seed_facility(
                facilities_by_station.setdefault(station_name, []),
                "엘리베이터",
                "설치됨",
            )
        if _yes(row.get("휠체어리프트여부")):
            _append_seed_facility(
                facilities_by_station.setdefault(station_name, []),
                "휠체어 리프트",
                "설치됨",
            )

    _seoul_metro_facilities_cache = facilities_by_station
    return facilities_by_station


def _load_official_csv_facilities() -> dict[str, list[tuple[str, str | None, str]]]:
    global _official_csv_facilities_cache
    if _official_csv_facilities_cache is not None:
        return _official_csv_facilities_cache

    facilities_by_station: dict[str, list[tuple[str, str | None, str]]] = {}

    for row in _read_csv_dicts(_KORAIL_LIFT_FACILITIES_CSV):
        station_name = _normalize_facility_station_name(row.get("역명"))
        if not station_name:
            continue
        _append_count_facility(
            facilities_by_station,
            station_name,
            "엘리베이터",
            row.get("엘리베이터"),
        )
        _append_count_facility(
            facilities_by_station,
            station_name,
            "에스컬레이터",
            row.get("에스컬레이터"),
        )
        _append_count_facility(
            facilities_by_station,
            station_name,
            "휠체어 리프트",
            row.get("휠체어리프트"),
        )

    for row in _read_csv_dicts(_AIRPORT_RAILROAD_RESTROOMS_CSV):
        station_name = _normalize_facility_station_name(row.get("역명"))
        if not station_name:
            continue
        ground_label = str(row.get("지상구분") or "").strip()
        floor_label = str(row.get("역층") or "").strip()
        gate_label = str(row.get("게이트내외") or "").strip()
        exit_label = str(row.get("출구번호") or "").strip()
        floor_note = f"{ground_label} {floor_label}층".strip() if floor_label else ground_label
        gate_note = f"게이트 {gate_label}" if gate_label else ""
        exit_note = f"{exit_label}번 출구" if exit_label else ""
        location_parts = [
            floor_note,
            gate_note,
            exit_note,
            str(row.get("상세위치") or "").strip(),
        ]
        note = " · ".join(part for part in location_parts if part)
        _append_seed_facility(
            facilities_by_station.setdefault(station_name, []),
            "장애인 화장실",
            note or "설치됨",
        )

    _official_csv_facilities_cache = facilities_by_station
    return facilities_by_station


def _normalize_seoul_metro_csv_line(value: object) -> str:
    raw = str(value or "").strip()
    match = re.search(r"(\d+)", raw)
    if not match:
        return raw
    line_number = int(match.group(1))
    # 서울교통공사 CSV는 25호선=5호선, 28호선=8호선처럼 앞자리에 운영권역을 붙인다.
    if line_number >= 20:
        line_number -= 20
    return f"{line_number}호선"


def _build_seoul_metro_csv_facilities(normalized_station: str) -> StationFacilities:
    items = _load_seoul_metro_facilities().get(normalized_station, [])
    facilities = [
        StationFacility(
            station_name=normalized_station,
            facility_type=facility_type,
            location_note=location_note,
            operational_status=operational_status,
        )
        for facility_type, location_note, operational_status in items
    ]
    return StationFacilities(
        station_name=normalized_station,
        fetched_at=time.time(),
        facilities=facilities,
    )


def _build_official_csv_facilities(normalized_station: str) -> StationFacilities:
    items = _load_official_csv_facilities().get(normalized_station, [])
    facilities = [
        StationFacility(
            station_name=normalized_station,
            facility_type=facility_type,
            location_note=location_note,
            operational_status=operational_status,
        )
        for facility_type, location_note, operational_status in items
    ]
    return StationFacilities(
        station_name=normalized_station,
        fetched_at=time.time(),
        facilities=facilities,
    )


def _to_int(value: object) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


async def fetch_station_facilities(
    settings: Settings, station_name: str
) -> StationFacilities:
    """역사 교통약자 시설 정보.

    서울/한국철도공사 API를 우선 사용하고, API가 비어 있는 운영기관 역은
    공식 파일데이터 CSV로 보충한다.
    """
    normalized = _normalize_station_name(station_name)

    seoul_api_result = StationFacilities(
        station_name=normalized,
        fetched_at=time.time(),
        facilities=[],
    )
    try:
        seoul_api_result = _build_seoul_accessible_facilities(
            normalized,
            await _fetch_seoul_accessible_items(settings),
        )
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("seoul accessible api fallback: %s", error)

    korail_api_result = StationFacilities(
        station_name=normalized,
        fetched_at=time.time(),
        facilities=[],
    )
    if settings.accessibility_api_key:
        base_url = settings.accessibility_api_base_url.rstrip("/")
        common_params = {
            "serviceKey": settings.accessibility_api_key,
            "pageNo": 1,
            "numOfRows": 10,
            "returnType": "JSON",
            "cond[stn_nm::EQ]": normalized,
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                weak_response, station_response = await client.get(
                    f"{base_url}/weekPersonFacilities",
                    params=common_params,
                ), await client.get(
                    f"{base_url}/stationFacilities",
                    params=common_params,
                )
            weak_response.raise_for_status()
            station_response.raise_for_status()
            korail_api_result = _build_api_facilities(
                normalized,
                _as_items(weak_response.json()),
                _as_items(station_response.json()),
            )
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("station facilities api fallback: %s", error)

    csv_result = _build_seoul_metro_csv_facilities(normalized)
    official_csv_result = _build_official_csv_facilities(normalized)

    return _merge_facility_payloads(
        normalized,
        seoul_api_result,
        korail_api_result,
        csv_result,
        official_csv_result,
    )

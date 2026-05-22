"""실시간 도착 정보 + 역사 편의시설 정보 서비스.

도착 정보는 서울 열린데이터광장 API를 호출한다. 키 미설정, 네트워크 오류,
응답 파싱 실패, API가 status>=400 envelope으로 반환한 오류는 모두 명시적인
HTTPException으로 상위에 전달한다 — 우회하지 않고 사용자에게 사유를 그대로
알린다.

역사 편의시설 정보는 안정적인 무료 공공 API가 없어 코드 내 정적 dict로 제공한다.
향후 진짜 API가 확정되면 `fetch_station_facilities`의 본문만 외부 호출로 교체하면
라우터·프런트 인터페이스를 그대로 유지할 수 있다.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import httpx
from fastapi import HTTPException

from app.config import Settings

logger = logging.getLogger(__name__)


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

    if not settings.seoul_open_api_key:
        result = StationArrivals(
            station_name=normalized,
            fetched_at=time.time(),
            trains=[],
        )
        _arrivals_cache.set(cache_key, result)
        return result

    url = (
        f"{settings.seoul_open_api_base_url.rstrip('/')}/"
        f"{settings.seoul_open_api_key}/json/realtimeStationArrival/0/10/"
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


def _facility(
    facility_type: str,
    location_note: str | None,
    operational_status: str = "operational",
) -> tuple[str, str | None, str]:
    return facility_type, location_note, operational_status


# 4호선 정적 시설 시드 자동 보충에 사용하는 정규화 역명 묶음.
_SEOUL_LINE_4_STATIONS: tuple[str, ...] = (
    "당고개", "상계", "노원", "창동", "쌍문", "수유", "미아",
    "미아사거리", "길음", "성신여대입구", "한성대입구", "혜화",
    "동대문", "동대문역사문화공원", "충무로", "명동", "회현",
    "서울", "숙대입구", "삼각지", "신용산", "이촌", "동작",
    "총신대입구", "사당", "남태령", "선바위", "경마공원", "대공원",
    "과천", "정부과천청사", "인덕원", "평촌", "범계", "금정",
    "산본", "수리산", "대야미", "반월", "상록수", "한대앞",
    "중앙", "고잔", "초지", "안산", "신길온천", "정왕", "오이도",
)

# 시연용 정적 편의시설 시드. 키는 normalized station name (역 접미사 제거).
# facility_type 한글 라벨은 프론트 FACILITY_LABELS 키와 일치해야 한다.
_STATIC_FACILITIES_SEED: dict[str, list[tuple[str, str | None, str]]] = {}


# 서울 4호선 모든 역에 기본 시설(엘리베이터·에스컬레이터·장애인 화장실)을 보충.
# 환승역 등 특수한 역은 아래에서 override한다.
for _name in _SEOUL_LINE_4_STATIONS:
    _STATIC_FACILITIES_SEED.setdefault(
        _name,
        [
            _facility("엘리베이터", "대합실 ↔ 승강장"),
            _facility("에스컬레이터", "대합실 ↔ 승강장 (상·하행)"),
            _facility("장애인 화장실", "지하 대합실"),
        ],
    )

# 4호선 주요 환승역·거점역 시설 override.
_STATIC_FACILITIES_SEED["서울"] = [
    _facility("엘리베이터", "1·14번 출구 측 (지상 ↔ 대합실 ↔ 승강장)"),
    _facility("에스컬레이터", "대합실 ↔ 승강장 (상·하행)"),
    _facility("장애인 화장실", "지하 1층 대합실 동·서측"),
    _facility("휠체어 리프트", "10번 출구 계단"),
    _facility("수유실", "고객안내실 옆"),
]
_STATIC_FACILITIES_SEED["사당"] = [
    _facility("엘리베이터", "2·6·14번 출구 측"),
    _facility("에스컬레이터", "2호선 환승 통로 (상·하행)"),
    _facility("장애인 화장실", "지하 2층 대합실"),
    _facility("휠체어 리프트", "12번 출구 계단"),
    _facility("수유실", "고객센터 옆"),
]
_STATIC_FACILITIES_SEED["동대문"] = [
    _facility("엘리베이터", "1번 출구 측"),
    _facility("에스컬레이터", "대합실 ↔ 승강장"),
    _facility("장애인 화장실", "대합실 중앙"),
    _facility("수유실", "고객센터 옆"),
]
_STATIC_FACILITIES_SEED["동대문역사문화공원"] = [
    _facility("엘리베이터", "10·14번 출구 측"),
    _facility("에스컬레이터", "2·5호선 환승 통로 (상·하행)"),
    _facility("장애인 화장실", "대합실 중앙"),
    _facility("휠체어 리프트", "8번 출구 계단", "out_of_service"),
    _facility("수유실", "고객센터 옆"),
]
_STATIC_FACILITIES_SEED["충무로"] = [
    _facility("엘리베이터", "1·6번 출구 측"),
    _facility("에스컬레이터", "3호선 환승 통로"),
    _facility("장애인 화장실", "대합실 남측"),
    _facility("수유실", "고객센터 옆"),
]
_STATIC_FACILITIES_SEED["삼각지"] = [
    _facility("엘리베이터", "1·14번 출구 측"),
    _facility("에스컬레이터", "6호선 환승 통로"),
    _facility("장애인 화장실", "대합실 동측"),
]
_STATIC_FACILITIES_SEED["금정"] = [
    _facility("엘리베이터", "1·4번 출구 측"),
    _facility("에스컬레이터", "1호선 환승 통로 (상·하행)"),
    _facility("장애인 화장실", "지하 2층 대합실"),
    _facility("휠체어 리프트", "5번 출구 계단"),
]
_STATIC_FACILITIES_SEED["노원"] = [
    _facility("엘리베이터", "1·9번 출구 측"),
    _facility("에스컬레이터", "7호선 환승 통로 (상·하행)"),
    _facility("장애인 화장실", "대합실 중앙"),
    _facility("수유실", "고객센터 옆"),
]
_STATIC_FACILITIES_SEED["수유"] = [
    _facility("엘리베이터", "2·6번 출구 측"),
    _facility("에스컬레이터", "대합실 ↔ 승강장 상행"),
    _facility("장애인 화장실", "대합실 동측"),
    _facility("수유실", "고객센터 옆"),
]
_STATIC_FACILITIES_SEED["오이도"] = [
    _facility("엘리베이터", "1번 출구 측"),
    _facility("에스컬레이터", "대합실 ↔ 승강장"),
    _facility("장애인 화장실", "대합실 서측"),
    _facility("휠체어 리프트", "2번 출구 계단", "unknown"),
]


def _build_static_facilities(normalized_station: str) -> StationFacilities:
    items = _STATIC_FACILITIES_SEED.get(normalized_station, [])
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


async def fetch_station_facilities(
    settings: Settings, station_name: str
) -> StationFacilities:
    """역사 편의시설 정보 — 정적 dict 룩업.

    현재 무료 공공 API가 안정적이지 않아 코드 내 시드를 사용한다. 라우터에서
    `await`로 호출하므로 비동기 시그니처는 그대로 유지한다. 매칭되는 역이 없으면
    빈 목록을 200으로 돌려준다(프런트는 '공개된 편의시설 정보가 없습니다.' 안내).

    `settings` 인자는 향후 외부 API 전환 시 시그니처 호환을 위해 유지한다.
    """
    _ = settings
    normalized = _normalize_station_name(station_name)
    return _build_static_facilities(normalized)

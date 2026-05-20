"""실시간 도착 정보 + 역사 편의시설 정보 서비스.

도착 정보는 서울 열린데이터광장 API를 호출한다. 키가 없거나 호출이 실패하면
시연·MVP가 멈추지 않도록 fallback 데모 데이터를 반환한다.

역사 편의시설 정보는 안정적인 무료 공공 API가 없어 코드 내 정적 dict로 제공한다.
향후 진짜 API가 확정되면 `fetch_station_facilities`의 본문만 외부 호출로 교체하면
라우터·프런트 인터페이스를 그대로 유지할 수 있다.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArrivalTrain:
    train_number: str | None
    destination: str
    eta_message: str
    direction: str | None
    line: str | None


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


def _normalize_station_name(name: str) -> str:
    """'인천대입구역' → '인천대입구' 형태로 정규화. 외부 API는 '역' 접미사
    유무가 들쑥날쑥하므로 호출 직전에 명시적으로 처리.
    """
    stripped = name.strip()
    if stripped.endswith("역"):
        return stripped[:-1]
    return stripped


def _parse_seoul_arrival_train(item: dict) -> ArrivalTrain:
    """서울 열린데이터 광장 realtimeStationArrival 응답 항목 한 건을 정규화."""
    destination = (item.get("bstatnNm") or item.get("trainLineNm") or "").strip()
    eta_message = (item.get("arvlMsg2") or item.get("arvlMsg3") or "").strip()
    direction = item.get("updnLine") or None
    line = item.get("subwayId") or item.get("subwayNm")
    train_number = item.get("btrainNo") or None
    return ArrivalTrain(
        train_number=train_number,
        destination=destination,
        eta_message=eta_message,
        direction=direction,
        line=str(line) if line is not None else None,
    )


def _build_fallback_arrivals(normalized_station: str) -> StationArrivals:
    """외부 API 호출이 불가능하거나 결과가 비어 있을 때 사용하는 데모 데이터.

    현재 STATION_SEED는 모두 인천1호선이므로 인천1호선 기준 4건을 반환한다.
    실 운영 키가 들어오면 자연스럽게 실시간 데이터로 대체된다.
    """
    line_label = "인천1호선"
    direction_labels = ["상행", "하행", "상행", "하행"]
    destinations = ["국제업무지구행", "계양행", "국제업무지구행", "계양행"]
    train_numbers = ["1146", "1144", "1142", "1140"]
    eta_messages = [
        "10분 후 도착",
        "3분 후 도착",
        "2분 전 출발",
        "9분 전 출발",
    ]
    trains = [
        ArrivalTrain(
            train_number=train_number,
            destination=destination,
            eta_message=eta_message,
            direction=direction,
            line=line_label,
        )
        for train_number, destination, eta_message, direction in zip(
            train_numbers,
            destinations,
            eta_messages,
            direction_labels,
            strict=True,
        )
    ]
    return StationArrivals(
        station_name=normalized_station,
        fetched_at=time.time(),
        trains=trains,
    )


async def fetch_station_arrivals(
    settings: Settings, station_name: str
) -> StationArrivals:
    """서울 열린데이터 광장 realtimeStationArrival 호출.

    키가 없거나 호출/파싱이 실패하면 fallback 데모 데이터를 반환한다(200 유지).
    """
    normalized = _normalize_station_name(station_name)
    cache_key = f"arrivals::{normalized}"
    cached = _arrivals_cache.get(cache_key, settings.transit_arrivals_cache_ttl)
    if isinstance(cached, StationArrivals):
        return cached

    if not settings.seoul_open_api_key:
        logger.info("seoul_open_api_key not set; serving fallback arrivals")
        return _build_fallback_arrivals(normalized)

    url = (
        f"{settings.seoul_open_api_base_url.rstrip('/')}/"
        f"{settings.seoul_open_api_key}/json/realtimeStationArrival/0/10/"
        f"{normalized}"
    )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
    except httpx.HTTPError as error:
        logger.warning("realtimeStationArrival http error: %s; serving fallback", error)
        return _build_fallback_arrivals(normalized)

    if response.status_code != 200:
        logger.warning(
            "realtimeStationArrival non-200 status %s; serving fallback",
            response.status_code,
        )
        return _build_fallback_arrivals(normalized)

    try:
        payload = response.json()
    except ValueError as error:
        logger.warning("realtimeStationArrival json error: %s; serving fallback", error)
        return _build_fallback_arrivals(normalized)

    items = payload.get("realtimeArrivalList") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        logger.info(
            "realtimeStationArrival returned no items for %s; serving fallback",
            normalized,
        )
        return _build_fallback_arrivals(normalized)

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


# 시연용 정적 편의시설 시드. 키는 normalized station name (역 접미사 제거).
# 모든 역은 인천1호선이며 facility_type 한글 라벨은 프론트 FACILITY_LABELS 키와 일치.
_STATIC_FACILITIES_SEED: dict[str, list[tuple[str, str | None, str]]] = {
    "계양": [
        _facility("엘리베이터", "1번 출구 ↔ 대합실 ↔ 승강장"),
        _facility("에스컬레이터", "2번 출구 측, 상행 전용"),
        _facility("장애인 화장실", "지하 1층 대합실 동측"),
        _facility("수유실", "지하 1층 고객센터 옆"),
    ],
    "귤현": [
        _facility("엘리베이터", "지상 ↔ 대합실 ↔ 승강장"),
        _facility("장애인 화장실", "대합실 서측"),
        _facility("휠체어 리프트", "1번 출구 계단", "out_of_service"),
    ],
    "박촌": [
        _facility("엘리베이터", "2번 출구 측"),
        _facility("에스컬레이터", "대합실 ↔ 승강장 (상·하행)"),
        _facility("장애인 화장실", "대합실 동측"),
    ],
    "임학": [
        _facility("엘리베이터", "1번 출구 ↔ 대합실"),
        _facility("장애인 화장실", "대합실 북측"),
        _facility("수유실", "고객센터 옆", "unknown"),
    ],
    "작전": [
        _facility("엘리베이터", "1·3번 출구 측"),
        _facility("에스컬레이터", "대합실 ↔ 승강장"),
        _facility("장애인 화장실", "지하 1층 대합실 남측"),
        _facility("휠체어 리프트", "2번 출구 계단 측"),
    ],
    "갈산": [
        _facility("엘리베이터", "2번 출구 ↔ 대합실 ↔ 승강장"),
        _facility("장애인 화장실", "대합실 서측"),
        _facility("에스컬레이터", "대합실 ↔ 승강장 상행", "out_of_service"),
    ],
    "지식정보단지": [
        _facility("엘리베이터", "1·2번 출구 모두"),
        _facility("에스컬레이터", "대합실 ↔ 승강장 (상·하행)"),
        _facility("장애인 화장실", "대합실 중앙"),
        _facility("수유실", "고객센터 옆"),
    ],
    "인천대입구": [
        _facility("엘리베이터", "1·3번 출구 측"),
        _facility("에스컬레이터", "대합실 ↔ 승강장 상·하행"),
        _facility("장애인 화장실", "지하 1층 대합실 동측"),
        _facility("휠체어 리프트", "2번 출구 계단"),
        _facility("수유실", "고객센터 인접"),
    ],
    "센트럴파크": [
        _facility("엘리베이터", "1번 출구 ↔ 대합실 ↔ 승강장"),
        _facility("에스컬레이터", "대합실 ↔ 승강장"),
        _facility("장애인 화장실", "대합실 남측"),
        _facility("수유실", "고객센터 옆"),
    ],
    "국제업무지구": [
        _facility("엘리베이터", "1·2번 출구 모두"),
        _facility("에스컬레이터", "대합실 ↔ 승강장"),
        _facility("장애인 화장실", "대합실 서측"),
    ],
    "송도달빛축제공원": [
        _facility("엘리베이터", "1번 출구 측"),
        _facility("장애인 화장실", "대합실 동측"),
        _facility("휠체어 리프트", "2번 출구 계단", "unknown"),
    ],
}


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

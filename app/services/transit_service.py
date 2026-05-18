"""실시간 도착 정보 + 역사 편의시설 외부 API 프록시.

외부 API 키는 `app.config.Settings`를 통해 환경 변수로만 주입한다. 코드에
하드코딩하지 않는다. 외부 호출 결과는 짧은 TTL의 메모리 캐시에 보관해 호출
빈도를 줄인다. 외부 호출 실패는 그대로 사용자에게 명시적인 에러로 전달하며,
빈 배열로 조용히 우회하지 않는다.
"""

from __future__ import annotations

import logging
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
_facilities_cache = _TTLCache()


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


async def fetch_station_arrivals(
    settings: Settings, station_name: str
) -> StationArrivals:
    """서울 열린데이터 광장 realtimeStationArrival 호출.

    문제 발생 시 우회하지 않고 HTTPException으로 상위에 명시적으로 전파한다.
    """
    if not settings.seoul_open_api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "실시간 도착 정보 API 키가 설정되어 있지 않습니다. "
                "GYOUM_SEOUL_OPEN_API_KEY 환경 변수를 설정해 주세요."
            ),
        )

    normalized = _normalize_station_name(station_name)
    cache_key = f"arrivals::{normalized}"
    cached = _arrivals_cache.get(cache_key, settings.transit_arrivals_cache_ttl)
    if isinstance(cached, StationArrivals):
        return cached

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
            detail="실시간 도착 정보를 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.",
        ) from error

    if response.status_code != 200:
        raise HTTPException(
            status_code=503,
            detail=(
                "실시간 도착 정보 응답이 비정상입니다 "
                f"(status={response.status_code})."
            ),
        )

    try:
        payload = response.json()
    except ValueError as error:
        raise HTTPException(
            status_code=503,
            detail="실시간 도착 정보 응답을 해석하지 못했습니다.",
        ) from error

    code = (
        payload.get("status")
        or payload.get("errorMessage", {}).get("status")
        if isinstance(payload.get("errorMessage"), dict)
        else None
    )
    if isinstance(code, int) and code >= 400:
        raise HTTPException(
            status_code=503,
            detail=(
                payload.get("errorMessage", {}).get("message")
                if isinstance(payload.get("errorMessage"), dict)
                else "실시간 도착 정보 응답이 오류를 반환했습니다."
            ),
        )

    items = payload.get("realtimeArrivalList") or []
    trains = [_parse_seoul_arrival_train(item) for item in items if isinstance(item, dict)]
    result = StationArrivals(
        station_name=normalized,
        fetched_at=time.time(),
        trains=trains,
    )
    _arrivals_cache.set(cache_key, result)
    return result


async def fetch_station_facilities(
    settings: Settings, station_name: str
) -> StationFacilities:
    """공공데이터포털 역사 편의시설 API 프록시.

    데이터셋·base URL이 사용자 결정에 따라 달라지므로, base URL이 비어 있으면
    503으로 명시적으로 알린다. 빈 배열을 조용히 반환하지 않는다.
    """
    if not settings.facility_api_key or not settings.facility_api_base_url:
        raise HTTPException(
            status_code=503,
            detail=(
                "역사 편의시설 API가 설정되어 있지 않습니다. "
                "GYOUM_FACILITY_API_KEY와 GYOUM_FACILITY_API_BASE_URL을 "
                "환경 변수로 설정해 주세요."
            ),
        )

    normalized = _normalize_station_name(station_name)
    cache_key = f"facilities::{normalized}"
    cached = _facilities_cache.get(cache_key, settings.transit_facilities_cache_ttl)
    if isinstance(cached, StationFacilities):
        return cached

    params = {
        "serviceKey": settings.facility_api_key,
        "stationName": normalized,
        "type": "json",
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(settings.facility_api_base_url, params=params)
    except httpx.HTTPError as error:
        logger.warning("facility api http error: %s", error)
        raise HTTPException(
            status_code=503,
            detail="역사 편의시설 정보를 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.",
        ) from error

    if response.status_code != 200:
        raise HTTPException(
            status_code=503,
            detail=f"역사 편의시설 응답이 비정상입니다 (status={response.status_code}).",
        )

    try:
        payload = response.json()
    except ValueError as error:
        raise HTTPException(
            status_code=503,
            detail="역사 편의시설 응답을 해석하지 못했습니다.",
        ) from error

    items = _extract_facility_items(payload)
    facilities = [_parse_facility_item(normalized, item) for item in items]
    result = StationFacilities(
        station_name=normalized,
        fetched_at=time.time(),
        facilities=facilities,
    )
    _facilities_cache.set(cache_key, result)
    return result


def _extract_facility_items(payload: object) -> list[dict]:
    """공공데이터포털 응답의 흔한 envelope에서 항목 리스트를 추출."""
    if not isinstance(payload, dict):
        return []
    response = payload.get("response")
    if isinstance(response, dict):
        body = response.get("body")
        if isinstance(body, dict):
            items = body.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
            if isinstance(items, dict):
                inner = items.get("item")
                if isinstance(inner, list):
                    return [item for item in inner if isinstance(item, dict)]
                if isinstance(inner, dict):
                    return [inner]
    items = payload.get("items") if isinstance(payload, dict) else None
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def _parse_facility_item(station_name: str, item: dict) -> StationFacility:
    """편의시설 항목 정규화. 공공데이터포털 데이터셋마다 키가 다르므로 흔한
    이름을 폭넓게 매핑한다.
    """
    facility_type = (
        item.get("facilityType")
        or item.get("equipmentType")
        or item.get("type")
        or item.get("kind")
        or "unknown"
    )
    location_note = (
        item.get("location")
        or item.get("position")
        or item.get("description")
        or None
    )
    raw_status = (
        item.get("operationalStatus")
        or item.get("status")
        or item.get("useYn")
        or ""
    )
    status_text = str(raw_status).strip().lower()
    if status_text in {"y", "yes", "ok", "정상", "운영", "operational"}:
        operational_status = "operational"
    elif status_text in {"n", "no", "out", "고장", "점검", "out_of_service"}:
        operational_status = "out_of_service"
    else:
        operational_status = "unknown"
    return StationFacility(
        station_name=station_name,
        facility_type=str(facility_type),
        location_note=str(location_note) if location_note else None,
        operational_status=operational_status,
    )

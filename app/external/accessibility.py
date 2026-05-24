from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET

import httpx

from app.schemas import SummaryFacilitiesResponse

_BASE = "https://apis.data.go.kr/B553766/wksn"

_DEFAULT_FACILITIES = SummaryFacilitiesResponse(
    accessible_toilet=False,
    elevator=False,
    wheelchair_lift=False,
    escalator=False,
)


async def fetch_facilities(station_name: str, api_key: str) -> SummaryFacilitiesResponse:
    if not api_key:
        return _DEFAULT_FACILITIES

    # API의 stnNm 필드는 "역" 없는 이름 (예: "한성대입구")
    name = station_name.removesuffix("역")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            elevator_items, toilet_items, lift_items, escalator_items = await asyncio.gather(
                _fetch_items(client, "/getWksnElvtr", api_key),
                _fetch_items(client, "/getWksnRstrm", api_key),
                _fetch_items(client, "/getWksnWhcllift", api_key),
                _fetch_items(client, "/getWksnEsctr", api_key),
            )
    except Exception:
        return _DEFAULT_FACILITIES

    def has(items: list) -> bool:
        return any(item.findtext("stnNm") == name for item in items)

    return SummaryFacilitiesResponse(
        accessible_toilet=has(toilet_items),
        elevator=has(elevator_items),
        wheelchair_lift=has(lift_items),
        escalator=has(escalator_items),
    )


async def _fetch_items(client: httpx.AsyncClient, path: str, api_key: str) -> list[ET.Element]:
    resp = await client.get(
        _BASE + path,
        params={"serviceKey": api_key, "numOfRows": "9999", "pageNo": "1"},
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    return root.findall(".//item")

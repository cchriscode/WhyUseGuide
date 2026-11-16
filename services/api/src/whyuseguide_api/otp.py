"""OTP(OpenTripPlanner) GTFS GraphQL `plan` 질의. 질의문과 응답 해석은 여기 한 곳에만 둔다.

API의 시간표 구간(timed.py)과 경로 벤치마크(data/pipelines)가 같이 쓴다.
요청이 실패하거나 응답을 해석할 수 없으면 OtpError를 던진다. 삼킬지 말지는 호출하는 쪽이 정한다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

PATH = "/otp/gtfs/v1"
WALK_ONLY: list[dict[str, str]] = [{"mode": "WALK"}]
WALK_TRANSIT: list[dict[str, str]] = [{"mode": "WALK"}, {"mode": "TRANSIT"}]
WALKING_BETTER = "WALKING_BETTER_THAN_TRANSIT"  # 경로 오류 코드: OTP가 걷는 게 낫다고 본다

PLAN_QUERY = """query($fl:Float!,$fo:Float!,$tl:Float!,$to:Float!,$date:String!,$time:String!,
             $modes:[TransportMode],$n:Int,$banned:String) {
  plan(from:{lat:$fl,lon:$fo}, to:{lat:$tl,lon:$to}, date:$date, time:$time, numItineraries:$n,
       transportModes:$modes, banned:{agencies:$banned}) {
    routingErrors { code }
    itineraries { startTime endTime duration numberOfTransfers
      legs { mode distance route { gtfsId agency { name } }
             fareProducts { id product { ... on DefaultFareProduct { price { amount } } } } } } } }"""

AGENCIES_QUERY = "{ agencies { gtfsId name } }"

Itinerary = Mapping[str, Any]


class OtpError(Exception):
    """OTP 요청 실패 또는 해석할 수 없는 응답."""


@dataclass(frozen=True)
class Plan:
    itineraries: list[Itinerary]
    errors: frozenset[str]  # routingErrors 코드


def plan_request(
    a: tuple[float, float],
    b: tuple[float, float],
    date: str,
    time: str,
    *,
    num: int,
    modes: Sequence[Mapping[str, str]] = WALK_TRANSIT,
    banned_agencies: str | None = None,
) -> dict[str, Any]:
    """a, b = (위도, 경도). date "YYYY-MM-DD", time "HH:MM"은 그래프의 현지 시각.

    banned_agencies: 쓰지 않을 운영사 gtfsId(쉼표 구분).
    """
    return {
        "query": PLAN_QUERY,
        "variables": {
            "fl": a[0],
            "fo": a[1],
            "tl": b[0],
            "to": b[1],
            "date": date,
            "time": time,
            "modes": list(modes),
            "n": num,
            "banned": banned_agencies,
        },
    }


def parse_plan(resp: httpx.Response) -> Plan:
    try:
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise OtpError(f"GraphQL 오류: {body['errors'][0].get('message')}")
        plan = body["data"]["plan"]
        return Plan(
            itineraries=list(plan["itineraries"]),
            errors=frozenset(e["code"] for e in plan.get("routingErrors") or []),
        )
    except httpx.HTTPStatusError as e:
        raise OtpError(f"HTTP {e.response.status_code}") from e
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise OtpError(f"응답을 해석할 수 없다: {type(e).__name__}: {e}") from e


async def fetch_plan(client: httpx.AsyncClient, base_url: str, request: Mapping[str, Any]) -> Plan:
    try:
        resp = await client.post(base_url + PATH, json=request)
    except httpx.HTTPError as e:
        raise OtpError(f"{type(e).__name__}: {e}") from e
    return parse_plan(resp)


def fetch_plan_sync(client: httpx.Client, base_url: str, request: Mapping[str, Any]) -> Plan:
    try:
        resp = client.post(base_url + PATH, json=request)
    except httpx.HTTPError as e:
        raise OtpError(f"{type(e).__name__}: {e}") from e
    return parse_plan(resp)


def fetch_agencies_sync(client: httpx.Client, base_url: str) -> dict[str, str]:
    """{운영사 이름: gtfsId}."""
    try:
        resp = client.post(base_url + PATH, json={"query": AGENCIES_QUERY})
        resp.raise_for_status()
        return {a["name"]: a["gtfsId"] for a in resp.json()["data"]["agencies"]}
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
        raise OtpError(f"운영사 목록을 받지 못했다: {type(e).__name__}: {e}") from e


def routes(it: Itinerary) -> list[Mapping[str, Any]]:
    """경로가 탄 노선들(걷기 구간 제외, 탄 순서)."""
    return [leg["route"] for leg in it["legs"] if leg["route"]]


def agency_names(it: Itinerary) -> set[str]:
    return {r["agency"]["name"] for r in routes(it)}


def fares(it: Itinerary) -> tuple[int, list[float]]:
    """(운임표로 계산된 요금 합, 운임표가 없는 승차 구간들의 km).

    OTP는 운영사 GTFS의 운임표(fare_attributes·fare_rules)로 구간마다 요금 상품을 붙인다. 한 번 내는 요금이
    여러 구간에 걸치면(같은 운영사 환승) 같은 id가 반복되므로 id별로 한 번만 센다. 운임표가 없는 운영사
    (근사 노선 등)의 구간은 상품이 없다. 그 구간의 거리를 돌려줘 호출자가 근사 운임으로 채우게 한다.
    """
    products: dict[str, float] = {}
    uncovered: list[float] = []
    for leg in it["legs"]:
        if not leg["route"]:
            continue
        priced = [p for p in leg.get("fareProducts") or [] if (p.get("product") or {}).get("price")]
        if not priced:
            uncovered.append(leg["distance"] / 1000)
        for p in priced:
            products[p["id"]] = p["product"]["price"]["amount"]
    return round(sum(products.values())), uncovered

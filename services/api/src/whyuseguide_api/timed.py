"""시간표 구간(PRD §5.3 OTP 질의): 열차 시각에 맞춘 출발 시각 안내.

구간마다 "출발 준비 시각"(앞 장소 체류가 끝나는 시각)에 OTP에 묻고, 가장 일찍 도착하는 경로를 고른다.
첫 열차를 기다리는 시간은 출발지에서 더 머무는 시간(wait_min)으로 두고, 구간 행에 출발 시각을 붙인다.

엔진은 depart_at이 실제 출발 준비 시각과 같은 시간표 구간만 쓴다. 앞 구간이 바뀌면 뒤 구간의 준비 시각도
바뀌므로, 준비 시각이 달라진 구간만 다시 묻기를 반복한다(구간 수 + 1번 안에 끝난다). OTP가 실패한 구간은
엔진이 근사한다(estimated, "예상").
"""

import asyncio
import math
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itinerary_engine.compute import compute, endpoints
from itinerary_engine.config import constants, round2, to_hhmm, to_min
from itinerary_engine.models import City, Day, DayResult, Leg, LegRow, PlanMode
from itinerary_engine.route import Endpoint, route_km, taxi_leg, transit_fare
from whyuseguide_api.db.models import Line

TZ = timezone(timedelta(hours=9))  # 도쿄. 도시가 늘면 cities에 시간대를 둔다.
PLAN = """query($fl:Float!,$fo:Float!,$tl:Float!,$to:Float!,
             $date:String!,$time:String!,$modes:[TransportMode]) {
  plan(from:{lat:$fl,lon:$fo}, to:{lat:$tl,lon:$to}, date:$date, time:$time, numItineraries:5,
       transportModes:$modes) {
    routingErrors { code }
    itineraries { startTime endTime numberOfTransfers
      legs { mode distance route { agency { name } } } } } }"""
WALK_ONLY = [{"mode": "WALK"}]
WALK_TRANSIT = [{"mode": "WALK"}, {"mode": "TRANSIT"}]
WALKING_BETTER = "WALKING_BETTER_THAN_TRANSIT"  # OTP가 경로 없이 이 오류만 줄 때가 있다
NO_STOPS = "NO_STOPS_IN_RANGE"  # 출발·도착 근처에 역이 없다(데이터 공백)


def ready_ms(date: str, ready: int) -> int:
    """그날 0시 기준 분 → epoch ms(도쿄 시간)."""
    d = datetime.fromisoformat(date).replace(tzinfo=TZ)
    return int((d + timedelta(minutes=ready)).timestamp() * 1000)


def to_leg(
    it: Mapping[str, Any],
    a: Endpoint,
    b: Endpoint,
    plan_mode: PlanMode,
    ready: int,
    start_ms: int,
    city: City,
    estimated_agencies: frozenset[str],
) -> Leg:
    """OTP 경로 → 시간표 구간. 출발은 내림, 도착은 올림(도착 시각을 앞당겨 보이지 않게)."""
    wait = max(0, (it["startTime"] - start_ms) // 60000)
    arrive = math.ceil((it["endTime"] - start_ms) / 60000)
    routes = [leg["route"] for leg in it["legs"] if leg["route"]]
    transit = bool(routes)
    return Leg.model_validate(
        {
            "from": a.id,
            "to": b.id,
            "planMode": plan_mode,
            "mode": "transit" if transit else "walk",
            "minutes": arrive - wait,
            "fare": transit_fare(route_km(a, b), city) if transit else 0,
            "km": round2(sum(leg["distance"] for leg in it["legs"]) / 1000),
            "estimated": any(r["agency"]["name"] in estimated_agencies for r in routes),
            "transfers": it["numberOfTransfers"] if transit else 0,
            "departAt": to_hhmm(ready),
            "waitMin": wait if transit else 0,
        }
    )


def agencies(it: Mapping[str, Any]) -> int:
    return len({leg["route"]["agency"]["name"] for leg in it["legs"] if leg["route"]})


class OtpPlanner:
    """OTP GTFS GraphQL로 시간표 구간을 구한다. 실패하면 None(엔진이 근사)."""

    def __init__(self, client: httpx.AsyncClient, url: str, concurrency: int = 8) -> None:
        self.client, self.url = client, f"{url}/otp/gtfs/v1"
        self.limit = asyncio.Semaphore(concurrency)

    async def plan(
        self, a: Endpoint, b: Endpoint, date: str, ready: int, modes: list[dict[str, str]]
    ) -> tuple[list[dict[str, Any]], set[str]] | None:
        """(경로들, 경로 오류 코드). 요청 자체가 실패하면 None."""
        variables = {
            "fl": a.lat,
            "fo": a.lon,
            "tl": b.lat,
            "to": b.lon,
            "date": date,
            "time": to_hhmm(ready),
            "modes": modes,
        }
        try:
            async with self.limit:
                r = await self.client.post(self.url, json={"query": PLAN, "variables": variables})
            r.raise_for_status()
            plan = r.json()["data"]["plan"]
            its: list[dict[str, Any]] = plan["itineraries"]
            errors = {e["code"] for e in plan.get("routingErrors") or []}
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return None
        return its, errors

    async def walk(
        self, a: Endpoint, b: Endpoint, date: str, ready: int, plan_mode: PlanMode, city: City
    ) -> Leg | None:
        res = await self.plan(a, b, date, ready, WALK_ONLY)
        if res is None or not res[0]:
            return None
        return to_leg(res[0][0], a, b, plan_mode, ready, ready_ms(date, ready), city, frozenset())

    async def leg(
        self,
        a: Endpoint,
        b: Endpoint,
        date: str,
        ready: int,
        plan_mode: PlanMode,
        city: City,
        estimated_agencies: frozenset[str],
    ) -> Leg | None:
        """이동 방식별 규칙(§5.3)은 근사 규칙과 같은 거리 기준을 쓴다.

        균형·알뜰: 경로 km가 도보 기준 이하면 도보, 넘으면 대중교통(도보만 하는 경로는 뺀다).
            OTP가 걷는 게 낫다고 하거나(WALKING_BETTER_THAN_TRANSIT) 대중교통이 모두 걷기보다 늦게
            도착하면 걷는다(예: 19분 기다려 40분 걷고 3분 타는 경로).
        모든 방식: 대중교통 없이 NO_STOPS_IN_RANGE(근처에 역이 없음)면 None → 엔진 근사("예상").
            버스 등이 데이터에 없어서 생기는 공백이라, OTP 도보 경로(몇 시간 걷기)를 쓰지 않는다.
        균형·빠르게: 가장 일찍 도착.
        알뜰: 운영사 수가 가장 적은 경로(운영사가 바뀌면 운임이 새로 붙는다), 같으면 일찍 도착.
        빠르게: 도보도 후보. 환승이 있고 경로 km가 TAXI_CMP_KM를 넘으면 근사 택시와 비교해 일찍 도착하는 쪽.
        """
        c = constants()
        km = route_km(a, b)
        start_ms = ready_ms(date, ready)
        walk_km = {"balance": c["walkOnlyKm"], "cheap": c["walkCheapKm"]}.get(plan_mode)
        if walk_km is not None and km <= walk_km:
            return await self.walk(a, b, date, ready, plan_mode, city)

        res = await self.plan(a, b, date, ready, WALK_TRANSIT)
        if res is None:
            return None
        its, errors = res
        transit = [it for it in its if any(leg["route"] for leg in it["legs"])]
        if not transit and NO_STOPS in errors:
            return None
        if not its:
            return await self.walk(a, b, date, ready, plan_mode, city) if WALKING_BETTER in errors else None
        if plan_mode != "fast":
            walks = [it for it in its if it not in transit]
            walk_first = walks and min(w["endTime"] for w in walks) <= min(
                t["endTime"] for t in transit or walks
            )
            if transit and not walk_first:
                its = transit
        if plan_mode == "cheap":
            best = min(its, key=lambda it: (agencies(it), it["endTime"]))
        else:
            best = min(its, key=lambda it: it["endTime"])
        leg = to_leg(best, a, b, plan_mode, ready, start_ms, city, estimated_agencies)
        if plan_mode == "fast" and leg.transfers >= 1 and km > c["taxiCompareKm"]:
            taxi = taxi_leg(km, city)
            if taxi.minutes < leg.wait_min + leg.minutes:
                return Leg.model_validate(
                    {
                        "from": a.id,
                        "to": b.id,
                        "planMode": plan_mode,
                        "mode": "taxi",
                        "minutes": taxi.minutes,
                        "fare": taxi.fare,
                        "km": taxi.km,
                        "estimated": True,
                        "departAt": to_hhmm(ready),
                    }
                )
        return leg


def planner_for(session: AsyncSession) -> OtpPlanner | None:
    """요청 세션에 실린 OTP 질의 객체(deps.get_session이 싣는다). 없으면 근사만 쓴다."""
    planner = session.info.get("planner")
    return planner if isinstance(planner, OtpPlanner) else None


async def estimated_agencies(session: AsyncSession) -> frozenset[str]:
    """근사 노선(배차 간격 기반 GTFS) 운영사 이름. OTP 경로가 이 운영사를 타면 "예상"."""
    rows = await session.execute(select(Line.operator).where(Line.estimated).distinct())
    return frozenset(r for (r,) in rows if r)


async def timed_compute(
    day: Day, planner: OtpPlanner | None, approx_agencies: frozenset[str] = frozenset()
) -> DayResult:
    """시간표 구간으로 일정 계산. 자동차 여행·OTP 없음이면 엔진 근사 그대로.

    결과는 (출발, 도착, 출발 준비 시각)마다 기억한다. 앞 구간이 아직 맞춰지는 중간 단계에서는 뒤 구간의
    준비 시각이 잠깐 달라졌다가 돌아올 수 있어서, 마지막 결과만 들고 있으면 맞는 결과를 잃는다.
    """
    if planner is None or day.transport == "car":
        return compute(day)
    points = {p.id: p for p in endpoints(day)}
    given = {(lg.from_id, lg.to_id): lg for lg in day.legs}  # 호출자가 준 구간(OTP 실패 때 쓴다)
    legs = dict(given)
    found: dict[tuple[str, str, str], Leg | None] = {}
    for _ in range(len(points) + 2):
        result = compute(day.model_copy(update={"legs": tuple(legs.values())}))
        todo: list[tuple[str, str, str]] = []
        changed = False
        for row in result.rows:
            if not isinstance(row, LegRow):
                continue
            key = (row.from_id, row.to_id, to_hhmm(to_min(row.depart) - row.wait_min))
            if key not in found:
                todo.append(key)
                continue
            want = found[key] or given.get(key[:2])
            if legs.get(key[:2]) is not want:
                changed = True
                if want is None:
                    legs.pop(key[:2], None)
                else:
                    legs[key[:2]] = want
        if todo:
            got = await asyncio.gather(
                *(
                    planner.leg(
                        points[f], points[t], day.date, to_min(r), day.plan_mode, day.city, approx_agencies
                    )
                    for f, t, r in todo
                )
            )
            for key, leg in zip(todo, got, strict=True):
                found[key] = leg
                want = leg or given.get(key[:2])
                if want is None:
                    legs.pop(key[:2], None)
                else:
                    legs[key[:2]] = want
        elif not changed:
            return result
    return compute(day.model_copy(update={"legs": tuple(legs.values())}))

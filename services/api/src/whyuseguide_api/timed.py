"""시간표 구간(PRD §5.3 OTP 질의): 열차 시각에 맞춘 출발 시각 안내.

구간마다 "출발 준비 시각"(앞 장소 체류가 끝나는 시각)에 OTP에 묻고, 가장 일찍 도착하는 경로를 고른다.
첫 열차를 기다리는 시간은 출발지에서 더 머무는 시간(wait_min)으로 두고, 구간 행에 출발 시각을 붙인다.

엔진은 depart_at이 실제 출발 준비 시각과 같은 시간표 구간만 쓴다. 앞 구간이 바뀌면 뒤 구간의 준비 시각도
바뀌므로, 준비 시각이 달라진 구간만 다시 묻기를 반복한다. OTP가 실패하거나 쓸 경로가 없는 구간은 엔진이
근사한다(estimated, "예상"). 실패는 경고 로그로 남긴다.

구성: 경로 고르기(pick_itinerary)·구간 변환(to_leg)은 순수 함수, OtpPlanner는 OTP 질의, timed_compute는 반복.
OTP 질의문·응답 해석은 otp.py.
"""

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itinerary_engine.compute import compute, endpoints
from itinerary_engine.config import constants, round2, to_hhmm, to_min
from itinerary_engine.models import City, Day, DayResult, Leg, LegRow, PlanMode
from itinerary_engine.route import Endpoint, route_km, taxi_leg, transit_fare
from whyuseguide_api import otp
from whyuseguide_api.db.models import City as CityRow
from whyuseguide_api.db.models import Line

log = logging.getLogger(__name__)

NUM_ITINERARIES = 5  # 구간마다 OTP에서 받는 후보 경로 수. 이 중에서 이동 방식 규칙으로 하나를 고른다.
MS_PER_MIN = 60_000


@dataclass(frozen=True)
class CityTransit:
    """시간표 구간 계산에 필요한 도시 정보(DB에서 읽는다)."""

    tz: tzinfo  # cities.timezone. OTP의 epoch 시각을 그날의 분으로 바꾸는 데 쓴다.
    estimated_agencies: frozenset[str]  # 근사 노선(배차 간격 기반 GTFS) 운영사 이름. 타면 "예상".


def ready_ms(date: str, ready: int, tz: tzinfo) -> int:
    """그날 0시 기준 분 → epoch ms."""
    d = datetime.fromisoformat(date).replace(tzinfo=tz)
    return int((d + timedelta(minutes=ready)).timestamp() * 1000)


def to_leg(
    it: otp.Itinerary, a: Endpoint, b: Endpoint, plan_mode: PlanMode, ready: int, start_ms: int, city: City
) -> Leg:
    """OTP 경로 → 시간표 구간(estimated는 호출자가 정한다).

    출발은 분 내림, 도착은 분 올림(도착 시각을 앞당겨 보이지 않게).
    """
    wait = max(0, (it["startTime"] - start_ms) // MS_PER_MIN)
    arrive = math.ceil((it["endTime"] - start_ms) / MS_PER_MIN)
    transit = bool(otp.routes(it))
    return Leg.model_validate(
        {
            "from": a.id,
            "to": b.id,
            "planMode": plan_mode,
            "mode": "transit" if transit else "walk",
            "minutes": arrive - wait,
            "fare": transit_fare(route_km(a, b), city) if transit else 0,
            "km": round2(sum(leg["distance"] for leg in it["legs"]) / 1000),
            "transfers": it["numberOfTransfers"] if transit else 0,
            "departAt": to_hhmm(ready),
            "waitMin": wait if transit else 0,
        }
    )


def pick_itinerary(itineraries: list[otp.Itinerary], plan_mode: PlanMode) -> otp.Itinerary:
    """이동 방식 규칙(§5.3)으로 후보 중 하나를 고른다. itineraries는 비어 있지 않다.

    균형·알뜰: 대중교통 경로를 쓴다. 다만 대중교통이 모두 걷기보다 늦게 도착하면 걷는다
        (예: 19분 기다려 40분 걷고 3분 타는 경로).
    균형·빠르게: 가장 일찍 도착. 빠르게는 걷기도 후보.
    알뜰: 운영사 수가 가장 적은 경로(운영사가 바뀌면 운임이 새로 붙는다), 같으면 일찍 도착.
    """
    candidates = itineraries
    if plan_mode != "fast":
        transit = [it for it in itineraries if otp.routes(it)]
        walks = [it for it in itineraries if not otp.routes(it)]
        walk_arrives_first = bool(walks and transit) and min(w["endTime"] for w in walks) <= min(
            t["endTime"] for t in transit
        )
        if transit and not walk_arrives_first:
            candidates = transit
    if plan_mode == "cheap":
        return min(candidates, key=lambda it: (len(otp.agency_names(it)), it["endTime"]))
    return min(candidates, key=lambda it: it["endTime"])


class OtpPlanner:
    """OTP로 시간표 구간을 구한다. 실패하거나 쓸 경로가 없으면 None(엔진이 근사)."""

    def __init__(self, client: httpx.AsyncClient, url: str, concurrency: int) -> None:
        self.client, self.url = client, url
        self.limit = asyncio.Semaphore(concurrency)

    async def plan(
        self, a: Endpoint, b: Endpoint, date: str, ready: int, modes: list[dict[str, str]]
    ) -> otp.Plan | None:
        request = otp.plan_request(
            (a.lat, a.lon), (b.lat, b.lon), date, to_hhmm(ready), num=NUM_ITINERARIES, modes=modes
        )
        try:
            async with self.limit:
                return await otp.fetch_plan(self.client, self.url, request)
        except otp.OtpError as e:
            log.warning(
                "OTP 질의 실패, 구간을 근사한다: %s → %s %s %s: %s", a.id, b.id, date, to_hhmm(ready), e
            )
            return None

    async def walk(
        self, a: Endpoint, b: Endpoint, date: str, ready: int, plan_mode: PlanMode, city: City, tz: tzinfo
    ) -> Leg | None:
        plan = await self.plan(a, b, date, ready, otp.WALK_ONLY)
        if plan is None or not plan.itineraries:
            return None
        return to_leg(plan.itineraries[0], a, b, plan_mode, ready, ready_ms(date, ready, tz), city)

    async def leg(
        self,
        a: Endpoint,
        b: Endpoint,
        date: str,
        ready: int,
        plan_mode: PlanMode,
        city: City,
        transit: CityTransit,
    ) -> Leg | None:
        """ready(분)에 출발 준비가 됐을 때의 구간. 이동 방식별 도보 기준은 근사 규칙(§5.3)과 같다.

        균형·알뜰: 경로 km가 도보 기준 이하면 OTP 도보 경로.
        모든 방식: 대중교통 경로가 하나도 없으면 OTP가 걷는 게 낫다고 할 때만 걷고, 아니면 None → 엔진 근사
            ("예상"). 근처에 역이 없거나(NO_STOPS_IN_RANGE) 그 시각에 다니는 버스가 없을 때 OTP는 몇 시간
            걷는 경로를 내는데, 데이터 공백이라 쓰지 않는다.
        빠르게: 환승이 있고 경로 km가 TAXI_CMP_KM를 넘으면 근사 택시와 비교해 일찍 도착하는 쪽
            (택시 운임이 있는 도시만).
        """
        c = constants()
        km = route_km(a, b)
        walk_km = {"balance": c["walkOnlyKm"], "cheap": c["walkCheapKm"]}.get(plan_mode)
        if walk_km is not None and km <= walk_km:
            return await self.walk(a, b, date, ready, plan_mode, city, transit.tz)

        plan = await self.plan(a, b, date, ready, otp.WALK_TRANSIT)
        if plan is None:
            return None
        if not any(otp.routes(it) for it in plan.itineraries):
            if otp.WALKING_BETTER not in plan.errors:
                return None
            if not plan.itineraries:
                return await self.walk(a, b, date, ready, plan_mode, city, transit.tz)
        best = pick_itinerary(plan.itineraries, plan_mode)
        leg = to_leg(best, a, b, plan_mode, ready, ready_ms(date, ready, transit.tz), city)
        if otp.agency_names(best) & transit.estimated_agencies:
            leg = leg.model_copy(update={"estimated": True})
        if plan_mode == "fast" and city.taxi and leg.transfers >= 1 and km > c["taxiCompareKm"]:
            taxi = taxi_leg(km, city.taxi)
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


async def city_transit(session: AsyncSession, city_id: uuid.UUID) -> CityTransit:
    tz_name = (await session.execute(select(CityRow.timezone).where(CityRow.id == city_id))).scalar_one()
    rows = await session.execute(
        select(Line.operator).where(Line.city_id == city_id, Line.estimated).distinct()
    )
    return CityTransit(tz=ZoneInfo(tz_name), estimated_agencies=frozenset(r for (r,) in rows if r))


async def timed_compute(day: Day, planner: OtpPlanner | None, transit: CityTransit) -> DayResult:
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

    def use(key: tuple[str, str, str]) -> bool:
        """이 준비 시각의 결과(없으면 호출자가 준 구간, 그것도 없으면 근사)를 쓰게 한다. 바뀌었으면 True."""
        want = found[key] or given.get(key[:2])
        if legs.get(key[:2]) is want:
            return False
        if want is None:
            legs.pop(key[:2], None)
        else:
            legs[key[:2]] = want
        return True

    rounds = len(points) + 2  # 앞 구간부터 하나씩 확정되므로 구간 수 + 여유 안에 끝난다
    for _ in range(rounds):
        result = compute(day.model_copy(update={"legs": tuple(legs.values())}))
        keys = [
            (row.from_id, row.to_id, to_hhmm(to_min(row.depart) - row.wait_min))
            for row in result.rows
            if isinstance(row, LegRow)
        ]
        todo = [k for k in keys if k not in found]
        changed = [use(k) for k in keys if k in found]
        if not todo and not any(changed):
            return result
        got = await asyncio.gather(
            *(
                planner.leg(points[f], points[t], day.date, to_min(r), day.plan_mode, day.city, transit)
                for f, t, r in todo
            )
        )
        for key, leg in zip(todo, got, strict=True):
            found[key] = leg
            use(key)
    log.warning("시간표 구간이 %d번 안에 맞춰지지 않았다: %s %s", rounds, day.date, day.plan_mode)
    return compute(day.model_copy(update={"legs": tuple(legs.values())}))

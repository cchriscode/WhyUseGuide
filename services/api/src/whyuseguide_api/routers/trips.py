"""여행·일정 API (PRD §7, F-01~F-11). 입력 검증과 엔드포인트만 둔다.

시간·순서·요금은 일정 엔진이 계산하고, 일정 읽기·쓰기·응답 모양은 itinerary 모듈이 맡는다.
"""

import asyncio
import uuid
from datetime import date, timedelta
from typing import Annotated, Any, Literal

import anyio
from fastapi import APIRouter, Body
from pydantic import Field
from sqlalchemy import select

from itinerary_engine.adjust import AdjustResult, auto_adjust
from itinerary_engine.generate import (
    PLAN_MODES,
    FixedBooking,
    GeneratedDay,
    GenerateInput,
    GenerateResult,
    Prefs,
    generate,
)
from itinerary_engine.models import Day as EngineDay
from itinerary_engine.models import DayResult, Item, PlanMode, Point
from itinerary_engine.suggest import suggest as engine_suggest
from whyuseguide_api import bridge, itinerary
from whyuseguide_api.db.models import (
    City,
    ItinerarySnapshot,
    Place,
    PlaceStatus,
    Trip,
    TripDay,
    TripFixedBooking,
)
from whyuseguide_api.deps import CurrentUser, Planner, Session
from whyuseguide_api.errors import ApiError
from whyuseguide_api.itinerary import DEFAULT_PLAN_MODE, Routing, SnapshotReason
from whyuseguide_api.schemas import HHMM, ApiModel, to_time

router = APIRouter(prefix="/v1/trips", tags=["trips"])

MIN_PLACES = 2  # 일정을 만들 수 있는 최소 장소 수


# --- 입력


class FixedBookingIn(ApiModel):
    place_id: uuid.UUID
    date: date
    time: HHMM


class PointIn(ApiModel):
    lat: float
    lon: float


class TripCreate(ApiModel):
    city: str
    start_date: date
    days: int = Field(ge=1, le=9)
    adults: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)
    companion: str | None = None
    tastes: list[str] = Field(min_length=1)
    pace: Literal["relaxed", "normal", "packed"] = "normal"
    budget: Literal["cheap", "normal", "luxury"] = "normal"
    mix: int = Field(default=50, ge=0, le=100)
    day_end_time: HHMM | None = None
    start_time: HHMM = "09:00"
    fixed_bookings: list[FixedBookingIn] = []
    lodging: PointIn | None = None


class SelectPlan(ApiModel):
    plan_mode: PlanMode


class Op(ApiModel):
    op: Literal["remove", "insert", "move", "stay", "mode", "delay"]
    position: int | None = None
    place_id: uuid.UUID | None = None
    from_: int | None = Field(default=None, alias="from")
    to: int | None = None
    stay_min: int | None = Field(default=None, ge=15, le=240)
    plan_mode: PlanMode | None = None
    gap: int | None = None
    minutes: int | None = Field(default=None, ge=0, le=600)


class DayPatch(ApiModel):
    ops: list[Op]
    auto_adjust: bool = True
    client_version: int


class SuggestIn(ApiModel):
    mode: Literal["replace", "between", "fill"]
    position: int | None = None


class CheckinIn(ApiModel):
    position: int = Field(ge=0)


# --- 일정 생성


async def shown_plans(
    routing: Routing | None,
    gen: GenerateInput,
    gday: GeneratedDay,
    ordered: dict[PlanMode, list[Item]],
) -> dict[PlanMode, DayResult]:
    """생성한 3안을 화면용으로 다시 계산한다: OTP 시간표 구간, 실패한 구간만 근사(§5.3)."""
    modes = list(ordered)
    results = await asyncio.gather(
        *(
            itinerary.shown(
                EngineDay(
                    date=gday.date,
                    start=gen.start,
                    items=tuple(ordered[mode]),
                    places=gen.places[gday.date],
                    city=gen.city,
                    plan_mode=mode,
                    transport=gen.transport,
                    start_point=gen.start_point,
                    end_point=gen.end_point,
                    day_end=gen.day_end,
                ),
                routing,
            )
            for mode in modes
        )
    )
    return dict(zip(modes, results, strict=True))


def plan_summary(days: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "moveMin": sum(d["totals"]["moveMin"] for d in days),
        "farePerPerson": sum(d["totals"]["farePerPerson"] for d in days),
        "spendPerPerson": sum(d["totals"]["spendPerPerson"] for d in days),
        "issues": sum(d["totals"]["issues"] for d in days),
        "ends": [d["totals"]["end"] for d in days],
    }


# --- 엔드포인트


@router.post("", status_code=201)
async def create_trip(
    body: TripCreate, session: Session, user: CurrentUser, planner: Planner
) -> dict[str, Any]:
    """여행 설정 저장 + 일정 3안 생성(F-01, F-02)."""
    city = (await session.execute(select(City).where(City.code == body.city))).scalar_one_or_none()
    if city is None:
        raise ApiError(422, "unknown_city", f"모르는 도시예요: {body.city}")
    dates = [body.start_date + timedelta(days=i) for i in range(body.days)]
    for fb in body.fixed_bookings:
        if fb.date not in dates:
            raise ApiError(422, "booking_outside_trip", "예약 날짜가 여행 기간 밖이에요")
    ids = await bridge.active_place_ids(session, city.id)
    if {fb.place_id for fb in body.fixed_bookings} - set(ids):
        raise ApiError(422, "unknown_place", "예약한 장소를 찾을 수 없어요")
    if len(ids) < MIN_PLACES:
        raise ApiError(422, "not_enough_places", "이 도시에는 아직 일정을 만들 장소가 부족해요")

    lodging = Point(lat=body.lodging.lat, lon=body.lodging.lon) if body.lodging else None
    gen_input = await itinerary.generate_input(
        session,
        city,
        dates,
        ids,
        tastes=body.tastes,
        start=body.start_time,
        pace=body.pace,
        mix=body.mix,
        fixed=tuple(
            FixedBooking(place_id=str(fb.place_id), date=fb.date.isoformat(), time=fb.time)
            for fb in body.fixed_bookings
        ),
        start_point=lodging,
        end_point=lodging,
        day_end=body.day_end_time,
    )
    result: GenerateResult = await anyio.to_thread.run_sync(generate, gen_input)

    trip = Trip(
        user_id=user.id,
        city_id=city.id,
        start_date=body.start_date,
        days=body.days,
        adults=body.adults,
        children=body.children,
        companion=body.companion,
        tastes=body.tastes,
        pace=body.pace,
        budget=body.budget,
        mix=body.mix,
        day_end_time=to_time(body.day_end_time) if body.day_end_time else None,
        prefs=Prefs().model_dump(),
    )
    session.add(trip)
    await session.flush()
    for fb in body.fixed_bookings:
        session.add(
            TripFixedBooking(trip_id=trip.id, place_id=fb.place_id, date=fb.date, time=to_time(fb.time))
        )
    point = f"SRID=4326;POINT({lodging.lon} {lodging.lat})" if lodging else None
    routing = await itinerary.routing_for(session, planner, city.id)
    plans: dict[str, list[dict[str, Any]]] = {m: [] for m in PLAN_MODES}
    for i, gday in enumerate(result.days):
        day = TripDay(
            trip_id=trip.id,
            day_index=i,
            date=dates[i],
            start_time=to_time(body.start_time),
            start_location=point,
            end_location=point,
        )
        session.add(day)
        await session.flush()
        await itinerary.save_items(session, day, list(gday.items))
        ordered = await itinerary.store_generated(session, day, gday)
        for mode, plan in (await shown_plans(routing, gen_input, gday, ordered)).items():
            plans[mode].append(
                {
                    "dayIndex": i,
                    "date": gday.date,
                    "totals": plan.totals.model_dump(by_alias=True, mode="json"),
                    "rows": itinerary.api_rows(plan),
                    "moveOver": gday.move_over,
                }
            )
    await session.commit()
    return {
        "tripId": str(trip.id),
        "plans": {m: {"summary": plan_summary(plans[m]), "days": plans[m]} for m in PLAN_MODES},
        "recommended": DEFAULT_PLAN_MODE,
    }


@router.get("/{trip_id}")
async def get_trip(
    trip_id: uuid.UUID, session: Session, user: CurrentUser, planner: Planner
) -> dict[str, Any]:
    trip = await itinerary.own_trip(session, user.id, trip_id)
    routing = await itinerary.routing_for(session, planner, trip.city_id)
    return await itinerary.trip_payload(session, routing, trip)


@router.post("/{trip_id}/select-plan")
async def select_plan(
    trip_id: uuid.UUID, body: SelectPlan, session: Session, user: CurrentUser, planner: Planner
) -> dict[str, Any]:
    """3안 중 하나를 고른다. 이후 그날의 기본 이동 방식이 된다(S-02)."""
    trip = await itinerary.own_trip(session, user.id, trip_id)
    days = (
        (await session.execute(select(TripDay).where(TripDay.trip_id == trip.id).order_by(TripDay.day_index)))
        .scalars()
        .all()
    )
    for day in days:
        snap = await itinerary.latest_snapshot(session, day, SnapshotReason.generate, body.plan_mode)
        day.plan_mode = body.plan_mode
        day.version += 1
        if snap is not None and day.cur_index < 0:
            await itinerary.save_items(session, day, itinerary.items_from(snap.items))
    trip.plan_mode = body.plan_mode
    await session.commit()
    routing = await itinerary.routing_for(session, planner, trip.city_id)
    return await itinerary.trip_payload(session, routing, trip)


def apply_ops(day: TripDay, items: list[Item], ops: list[Op]) -> list[Item]:
    """편집 연산을 순서대로 적용한다. 다녀온 곳(0..cur)은 바꿀 수 없다."""
    items = list(items)
    cur = day.cur_index

    def check(pos: int | None, allow_end: bool = False) -> int:
        limit = len(items) if allow_end else len(items) - 1
        if pos is None or not (cur < pos <= limit):
            raise ApiError(422, "invalid_position", f"바꿀 수 없는 위치예요: {pos}")
        return pos

    for op in ops:
        if op.op == "remove":
            items.pop(check(op.position))
        elif op.op == "insert":
            if op.place_id is None:
                raise ApiError(422, "invalid_op", "insert에는 placeId가 필요해요")
            items.insert(check(op.position, allow_end=True), Item(place_id=str(op.place_id)))
        elif op.op == "move":
            moved = items.pop(check(op.from_))
            items.insert(check(op.to, allow_end=True), moved)
        elif op.op == "stay":
            idx = next((i for i, it in enumerate(items) if it.place_id == str(op.place_id)), None)
            if idx is None or op.stay_min is None:
                raise ApiError(422, "invalid_op", "stay에는 일정에 있는 placeId와 stayMin이 필요해요")
            items[idx] = items[idx].model_copy(update={"stay_min": op.stay_min})
        elif op.op == "mode":
            if op.plan_mode is None:
                raise ApiError(422, "invalid_op", "mode에는 planMode가 필요해요")
            day.plan_mode = op.plan_mode
        elif op.op == "delay":
            if op.gap is None or op.minutes is None:
                raise ApiError(422, "invalid_op", "delay에는 gap과 minutes가 필요해요")
            day.delay_gap, day.delay_min = op.gap, op.minutes
    return items


@router.patch("/{trip_id}/days/{day_index}")
async def patch_day(
    trip_id: uuid.UUID, day_index: int, body: DayPatch, session: Session, user: CurrentUser, planner: Planner
) -> dict[str, Any]:
    """일정 변경 → 서버 재계산 + 자동 조정(F-04~F-06). 버전이 다르면 409와 최신 일정."""
    trip = await itinerary.own_trip(session, user.id, trip_id)
    day = await itinerary.get_day(session, trip, day_index, lock=True)
    routing = await itinerary.routing_for(session, planner, trip.city_id)
    if body.client_version != day.version:
        latest = await itinerary.render_day(session, routing, trip, day)
        raise ApiError(409, "version_conflict", "다른 곳에서 일정이 바뀌었어요", {"latest": latest})
    for op in body.ops:
        if op.op == "insert" and op.place_id is not None:
            place = await session.get(Place, op.place_id)
            if place is None or place.status != PlaceStatus.active or place.city_id != trip.city_id:
                raise ApiError(422, "unknown_place", "넣을 장소를 찾을 수 없어요")
    edited = apply_ops(day, await itinerary.day_items(session, day), body.ops)
    adjust: AdjustResult | None = None
    final = edited
    if body.auto_adjust:
        adjust = auto_adjust(await itinerary.engine_day(session, trip, day, edited), day.cur_index)
        if adjust.applied:
            session.add(
                ItinerarySnapshot(
                    trip_day_id=day.id,
                    items=itinerary.items_json(edited),
                    reason=SnapshotReason.auto_adjust,
                    messages=list(adjust.messages),
                )
            )
            final = list(adjust.items)
    await itinerary.save_items(session, day, final)
    day.version += 1
    await session.commit()
    return await itinerary.render_day(session, routing, trip, day, adjust)


@router.post("/{trip_id}/days/{day_index}/undo")
async def undo(
    trip_id: uuid.UUID, day_index: int, session: Session, user: CurrentUser, planner: Planner
) -> dict[str, Any]:
    """직전 자동 조정을 되돌린다(F-06)."""
    trip = await itinerary.own_trip(session, user.id, trip_id)
    day = await itinerary.get_day(session, trip, day_index, lock=True)
    snap = await itinerary.undo_snapshot(session, day)
    if snap is None:
        raise ApiError(409, "nothing_to_undo", "되돌릴 자동 조정이 없어요")
    await itinerary.save_items(session, day, itinerary.items_from(snap.items))
    await session.delete(snap)
    day.version += 1
    await session.commit()
    routing = await itinerary.routing_for(session, planner, trip.city_id)
    return await itinerary.render_day(session, routing, trip, day)


def suggest_position(mode: str, position: int | None, cur: int, count: int) -> int | None:
    """추천 위치 검증. replace는 아직 안 간 장소의 자리, between은 그 앞 구간(맨 뒤 포함)이어야 한다."""
    if mode == "fill":
        return None
    limit = count - 1 if mode == "replace" else count
    if position is None or not (cur < position <= limit):
        raise ApiError(422, "invalid_position", f"추천할 수 없는 위치예요: {position}")
    return position


@router.post("/{trip_id}/days/{day_index}/suggest")
async def suggest(
    trip_id: uuid.UUID, day_index: int, body: SuggestIn, session: Session, user: CurrentUser
) -> dict[str, Any]:
    """추천 후보(F-07~F-09): replace(대체) / between(사이 들르기) / fill(남는 시간)."""
    trip = await itinerary.own_trip(session, user.id, trip_id)
    day = await itinerary.get_day(session, trip, day_index)
    items = await itinerary.day_items(session, day)
    position = suggest_position(body.mode, body.position, day.cur_index, len(items))
    pool_ids = await bridge.nearby_place_ids(session, trip.city_id, [uuid.UUID(it.place_id) for it in items])
    pool = list((await bridge.engine_places(session, pool_ids, day.date)).values())
    edays = await itinerary.engine_day(session, trip, day, items)
    options = await anyio.to_thread.run_sync(
        lambda: engine_suggest(edays, body.mode, pool, position=position, cur=day.cur_index)
    )
    names = [edays.places[it.place_id].name for it in items]
    if body.mode == "replace" and position is not None:
        title = f"{names[position]} 대신 갈 곳"
    elif body.mode == "between" and position is not None:
        before = names[position - 1] if position > 0 else "출발"
        after = names[position] if position < len(names) else "도착"
        title = f"{before} → {after} 사이"
    else:
        title = "남는 시간에 들를 곳"
    return {
        "mode": body.mode,
        "title": title,
        "options": [o.model_dump(by_alias=True, mode="json") for o in options],
    }


@router.post("/{trip_id}/days/{day_index}/checkin")
async def checkin(
    trip_id: uuid.UUID,
    day_index: int,
    body: Annotated[CheckinIn, Body()],
    session: Session,
    user: CurrentUser,
    planner: Planner,
) -> dict[str, Any]:
    """도착 체크(F-11): 지금 있는 장소를 앞으로 옮긴다. 이후 자동 조정은 이 장소까지를 건드리지 않는다."""
    trip = await itinerary.own_trip(session, user.id, trip_id)
    day = await itinerary.get_day(session, trip, day_index, lock=True)
    items = await itinerary.day_items(session, day)
    if not (day.cur_index < body.position < len(items)):
        raise ApiError(422, "invalid_position", "도착 체크할 수 없는 위치예요")
    day.cur_index = body.position
    await itinerary.save_items(session, day, items)
    day.version += 1
    await session.commit()
    routing = await itinerary.routing_for(session, planner, trip.city_id)
    return await itinerary.render_day(session, routing, trip, day)

"""여행·일정 API (PRD §7, F-01~F-11). 시간·순서·요금은 일정 엔진이 계산하고, 여기서는 저장과 변환만 한다."""

import asyncio
import uuid
from datetime import date, time, timedelta
from typing import Annotated, Any, Literal

import anyio
from fastapi import APIRouter, Body
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itinerary_engine.adjust import AdjustResult, auto_adjust
from itinerary_engine.compute import compute
from itinerary_engine.generate import FixedBooking, GenerateInput, GenerateResult, Prefs, generate
from itinerary_engine.models import Day as EngineDay
from itinerary_engine.models import DayResult, Item, LegRow, Point, StopRow
from itinerary_engine.suggest import suggest as engine_suggest
from whyuseguide_api import bridge, timed
from whyuseguide_api.db.models import (
    City,
    ItineraryItem,
    ItinerarySnapshot,
    Place,
    PlaceStatus,
    Trip,
    TripDay,
    TripFixedBooking,
)
from whyuseguide_api.deps import CurrentUser, Session
from whyuseguide_api.errors import ApiError

router = APIRouter(prefix="/v1/trips", tags=["trips"])

PlanMode = Literal["fast", "balance", "cheap"]
PLAN_MODES: tuple[PlanMode, ...] = ("fast", "balance", "cheap")
LEG_LABELS = {"walk": "도보", "transit": "대중교통", "taxi": "택시", "car": "자동차"}


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


HHMM = Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")]


# --- 입력


class FixedBookingIn(Model):
    place_id: uuid.UUID
    date: date
    time: HHMM


class PointIn(Model):
    lat: float
    lon: float


class TripCreate(Model):
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


class SelectPlan(Model):
    plan_mode: PlanMode


class Op(Model):
    op: Literal["remove", "insert", "move", "stay", "mode", "delay"]
    position: int | None = None
    place_id: uuid.UUID | None = None
    from_: int | None = Field(default=None, alias="from")
    to: int | None = None
    stay_min: int | None = Field(default=None, ge=15, le=240)
    plan_mode: PlanMode | None = None
    gap: int | None = None
    minutes: int | None = Field(default=None, ge=0, le=600)


class DayPatch(Model):
    ops: list[Op]
    auto_adjust: bool = True
    client_version: int


class SuggestIn(Model):
    mode: Literal["replace", "between", "fill"]
    position: int | None = None


class CheckinIn(Model):
    position: int = Field(ge=0)


# --- 변환


def _t(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def hhmm(t: time) -> str:
    return t.strftime("%H:%M")


def api_rows(result: DayResult) -> list[dict[str, Any]]:
    """엔진 행 → API 행(PRD §7 응답). 구간 행에는 표시용 라벨을 붙인다."""
    out: list[dict[str, Any]] = []
    for r in result.rows:
        if isinstance(r, StopRow):
            out.append(r.model_dump(by_alias=True, mode="json"))
        elif isinstance(r, LegRow):
            row = r.model_dump(by_alias=True, mode="json")
            label = r.label or LEG_LABELS[r.mode]
            if r.mode == "transit" and r.transfers and not r.label:
                label += f" · 환승 {r.transfers}회"
            row["label"] = label
            out.append(row)
    return out


def day_payload(
    day: TripDay,
    result: DayResult,
    *,
    adjust: AdjustResult | None = None,
    undo_available: bool = False,
    baseline: DayResult | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": day.version,
        "dayIndex": day.day_index,
        "day": {
            "date": day.date.isoformat(),
            "start": hhmm(day.start_time),
            "curIndex": day.cur_index,
            "planMode": day.plan_mode,
            "totals": result.totals.model_dump(by_alias=True, mode="json"),
            "rows": api_rows(result),
        },
        "adjust": None,
        "baseline": None,
    }
    if adjust is not None:
        payload["adjust"] = {
            "applied": adjust.applied,
            "messages": list(adjust.messages),
            "dropSuggestion": adjust.drop_suggestion.model_dump(by_alias=True)
            if adjust.drop_suggestion
            else None,
            "fillGap": adjust.fill_gap.model_dump(by_alias=True) if adjust.fill_gap else None,
            "undoAvailable": undo_available,
        }
    if baseline is not None:
        payload["baseline"] = {"moveMin": baseline.totals.move_min, "end": baseline.totals.end}
    return payload


# --- DB 읽기·쓰기


async def own_trip(session: AsyncSession, user_id: uuid.UUID, trip_id: uuid.UUID) -> Trip:
    trip = await session.get(Trip, trip_id)
    if trip is None or trip.user_id != user_id:
        raise ApiError(404, "not_found", "여행을 찾을 수 없어요")
    return trip


async def get_day(session: AsyncSession, trip: Trip, day_index: int, lock: bool = False) -> TripDay:
    stmt = select(TripDay).where(TripDay.trip_id == trip.id, TripDay.day_index == day_index)
    if lock:
        stmt = stmt.with_for_update()
    day = (await session.execute(stmt)).scalar_one_or_none()
    if day is None:
        raise ApiError(404, "not_found", "그날 일정을 찾을 수 없어요")
    return day


async def day_items(session: AsyncSession, day: TripDay) -> list[Item]:
    rows = (
        await session.execute(
            select(ItineraryItem).where(ItineraryItem.trip_day_id == day.id).order_by(ItineraryItem.position)
        )
    ).scalars()
    return [
        Item(place_id=str(r.place_id), stay_min=r.stay_min, fixed_at=hhmm(r.fixed_at) if r.fixed_at else None)
        for r in rows
    ]


async def save_items(session: AsyncSession, day: TripDay, items: list[Item]) -> None:
    await session.execute(delete(ItineraryItem).where(ItineraryItem.trip_day_id == day.id))
    for pos, it in enumerate(items):
        session.add(
            ItineraryItem(
                trip_day_id=day.id,
                position=pos,
                place_id=uuid.UUID(it.place_id),
                stay_min=it.stay_min,
                fixed_at=_t(it.fixed_at) if it.fixed_at else None,
                status="done" if pos <= day.cur_index else "planned",
            )
        )


def items_json(items: list[Item] | tuple[Item, ...]) -> list[dict[str, Any]]:
    return [it.model_dump(by_alias=True) for it in items]


def _items_from(data: list[dict[str, Any]]) -> list[Item]:
    return [Item.model_validate(x) for x in data]


async def engine_day(session: AsyncSession, trip: Trip, day: TripDay, items: list[Item]) -> EngineDay:
    places = await bridge.engine_places(session, [uuid.UUID(it.place_id) for it in items], day.date)
    lodging = await session.execute(
        select(
            func.ST_Y(func.geometry(TripDay.start_location)),
            func.ST_X(func.geometry(TripDay.start_location)),
            func.ST_Y(func.geometry(TripDay.end_location)),
            func.ST_X(func.geometry(TripDay.end_location)),
        ).where(TripDay.id == day.id)
    )
    s_lat, s_lon, e_lat, e_lon = lodging.one()
    return EngineDay(
        date=day.date.isoformat(),
        start=hhmm(day.start_time),
        items=tuple(items),
        places=places,
        city=await bridge.city_network(session, trip.city_id),
        plan_mode=day.plan_mode,
        delay={"gap": day.delay_gap, "minutes": day.delay_min} if day.delay_gap is not None else None,
        start_point=Point(lat=s_lat, lon=s_lon) if s_lat is not None else None,
        end_point=Point(lat=e_lat, lon=e_lon) if e_lat is not None else None,
        day_end=hhmm(trip.day_end_time) if trip.day_end_time else None,
    )


async def baseline_for(session: AsyncSession, trip: Trip, day: TripDay) -> DayResult | None:
    """처음 생성한 일정(그날 이동 방식)의 결과. S-03 "처음 코스와 비교"."""
    snap = (
        await session.execute(
            select(ItinerarySnapshot)
            .where(
                ItinerarySnapshot.trip_day_id == day.id,
                ItinerarySnapshot.reason == "generate",
                ItinerarySnapshot.plan_mode == day.plan_mode,
            )
            .order_by(ItinerarySnapshot.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if snap is None:
        return None
    return await timed_result(session, await engine_day(session, trip, day, _items_from(snap.items)))


async def undo_snapshot(session: AsyncSession, day: TripDay) -> ItinerarySnapshot | None:
    return (
        await session.execute(
            select(ItinerarySnapshot)
            .where(ItinerarySnapshot.trip_day_id == day.id, ItinerarySnapshot.reason == "auto_adjust")
            .order_by(ItinerarySnapshot.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def timed_result(session: AsyncSession, eday: EngineDay) -> DayResult:
    """화면에 보여 줄 계산: OTP 시간표 구간(열차에 맞춘 출발 시각), 실패한 구간만 근사(§5.3)."""
    planner = timed.planner_for(session)
    if planner is None:
        return compute(eday)
    return await timed.timed_compute(eday, planner, await timed.estimated_agencies(session))


async def render_day(
    session: AsyncSession, trip: Trip, day: TripDay, adjust: AdjustResult | None = None
) -> dict[str, Any]:
    items = await day_items(session, day)
    result = await timed_result(session, await engine_day(session, trip, day, items))
    return day_payload(
        day,
        result,
        adjust=adjust,
        undo_available=await undo_snapshot(session, day) is not None,
        baseline=await baseline_for(session, trip, day),
    )


async def trip_payload(session: AsyncSession, trip: Trip) -> dict[str, Any]:
    days = (
        await session.execute(select(TripDay).where(TripDay.trip_id == trip.id).order_by(TripDay.day_index))
    ).scalars()
    return {
        "tripId": str(trip.id),
        "startDate": trip.start_date.isoformat(),
        "days": [await render_day(session, trip, d) for d in days],
        "planMode": trip.plan_mode,
        "pace": trip.pace,
        "tastes": list(trip.tastes),
    }


# --- 엔드포인트


@router.post("", status_code=201)
async def create_trip(body: TripCreate, session: Session, user: CurrentUser) -> dict[str, Any]:
    """여행 설정 저장 + 일정 3안 생성(F-01, F-02)."""
    city = (await session.execute(select(City).where(City.code == body.city))).scalar_one_or_none()
    if city is None:
        raise ApiError(422, "unknown_city", f"모르는 도시예요: {body.city}")
    dates = [body.start_date + timedelta(days=i) for i in range(body.days)]
    for fb in body.fixed_bookings:
        if fb.date not in dates:
            raise ApiError(422, "booking_outside_trip", "예약 날짜가 여행 기간 밖이에요")
    ids = await bridge.active_place_ids(session, city.id)
    booked = [fb.place_id for fb in body.fixed_bookings]
    missing = set(booked) - set(ids)
    if missing:
        raise ApiError(422, "unknown_place", "예약한 장소를 찾을 수 없어요")
    if len(ids) < 2:
        raise ApiError(422, "not_enough_places", "이 도시에는 아직 일정을 만들 장소가 부족해요")

    lodging = Point(lat=body.lodging.lat, lon=body.lodging.lon) if body.lodging else None
    gen_input = GenerateInput(
        dates=tuple(d.isoformat() for d in dates),
        tastes=tuple(body.tastes),
        places={d.isoformat(): await bridge.engine_places(session, ids, d) for d in dates},
        profiles=await bridge.profiles(session, ids),
        start=body.start_time,
        pace=body.pace,
        mix=body.mix,
        fixed=tuple(
            FixedBooking(place_id=str(fb.place_id), date=fb.date.isoformat(), time=fb.time)
            for fb in body.fixed_bookings
        ),
        city=await bridge.city_network(session, city.id),
        city_move_cap_factor=float(city.move_cap_factor),
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
        day_end_time=_t(body.day_end_time) if body.day_end_time else None,
        prefs=Prefs().model_dump(),
    )
    session.add(trip)
    await session.flush()
    for fb in body.fixed_bookings:
        session.add(TripFixedBooking(trip_id=trip.id, place_id=fb.place_id, date=fb.date, time=_t(fb.time)))
    point = f"SRID=4326;POINT({lodging.lon} {lodging.lat})" if lodging else None
    plans: dict[str, list[dict[str, Any]]] = {m: [] for m in PLAN_MODES}
    planner = timed.planner_for(session)
    approx_agencies = await timed.estimated_agencies(session) if planner else frozenset[str]()
    for i, gday in enumerate(result.days):
        day = TripDay(
            trip_id=trip.id,
            day_index=i,
            date=dates[i],
            start_time=_t(body.start_time),
            start_location=point,
            end_location=point,
        )
        session.add(day)
        await session.flush()
        await save_items(session, day, list(gday.items))
        modes = list(gday.plans)
        ordered: dict[str, list[Item]] = {}
        for mode, plan in gday.plans.items():
            by_id = {it.place_id: it for it in gday.items}
            ordered[mode] = [by_id[r.place_id] for r in plan.rows if isinstance(r, StopRow)]
            session.add(
                ItinerarySnapshot(
                    trip_day_id=day.id,
                    items=items_json(ordered[mode]),
                    reason="generate",
                    plan_mode=mode,
                )
            )
        # 3안 모두 화면용으로 다시 계산: OTP 시간표 구간(열차에 맞춘 출발 시각), 실패한 구간만 근사
        shown = await asyncio.gather(
            *(
                timed.timed_compute(
                    EngineDay(
                        date=gday.date,
                        start=body.start_time,
                        items=tuple(ordered[mode]),
                        places=gen_input.places[gday.date],
                        city=gen_input.city,
                        plan_mode=mode,
                        transport=gen_input.transport,
                        start_point=lodging,
                        end_point=lodging,
                        day_end=body.day_end_time,
                    ),
                    planner,
                    approx_agencies,
                )
                for mode in modes
            )
        )
        for mode, plan in zip(modes, shown, strict=True):
            plans[mode].append(
                {
                    "dayIndex": i,
                    "date": gday.date,
                    "totals": plan.totals.model_dump(by_alias=True, mode="json"),
                    "rows": api_rows(plan),
                    "moveOver": gday.move_over,
                }
            )
    await session.commit()

    def summary(days: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "moveMin": sum(d["totals"]["moveMin"] for d in days),
            "farePerPerson": sum(d["totals"]["farePerPerson"] for d in days),
            "spendPerPerson": sum(d["totals"]["spendPerPerson"] for d in days),
            "issues": sum(d["totals"]["issues"] for d in days),
            "ends": [d["totals"]["end"] for d in days],
        }

    return {
        "tripId": str(trip.id),
        "plans": {m: {"summary": summary(plans[m]), "days": plans[m]} for m in PLAN_MODES},
        "recommended": "balance",
    }


@router.get("/{trip_id}")
async def get_trip(trip_id: uuid.UUID, session: Session, user: CurrentUser) -> dict[str, Any]:
    return await trip_payload(session, await own_trip(session, user.id, trip_id))


@router.post("/{trip_id}/select-plan")
async def select_plan(
    trip_id: uuid.UUID, body: SelectPlan, session: Session, user: CurrentUser
) -> dict[str, Any]:
    """3안 중 하나를 고른다. 이후 그날의 기본 이동 방식이 된다(S-02)."""
    trip = await own_trip(session, user.id, trip_id)
    days = (
        (await session.execute(select(TripDay).where(TripDay.trip_id == trip.id).order_by(TripDay.day_index)))
        .scalars()
        .all()
    )
    for day in days:
        snap = (
            await session.execute(
                select(ItinerarySnapshot).where(
                    ItinerarySnapshot.trip_day_id == day.id,
                    ItinerarySnapshot.reason == "generate",
                    ItinerarySnapshot.plan_mode == body.plan_mode,
                )
            )
        ).scalar_one_or_none()
        day.plan_mode = body.plan_mode
        day.version += 1
        if snap is not None and day.cur_index < 0:
            await save_items(session, day, _items_from(snap.items))
    trip.plan_mode = body.plan_mode
    await session.commit()
    return await trip_payload(session, trip)


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
    trip_id: uuid.UUID, day_index: int, body: DayPatch, session: Session, user: CurrentUser
) -> dict[str, Any]:
    """일정 변경 → 서버 재계산 + 자동 조정(F-04~F-06). 버전이 다르면 409와 최신 일정."""
    trip = await own_trip(session, user.id, trip_id)
    day = await get_day(session, trip, day_index, lock=True)
    if body.client_version != day.version:
        latest = await render_day(session, trip, day)
        raise ApiError(409, "version_conflict", "다른 곳에서 일정이 바뀌었어요", {"latest": latest})
    for op in body.ops:
        if op.op == "insert" and op.place_id is not None:
            place = await session.get(Place, op.place_id)
            if place is None or place.status != PlaceStatus.active or place.city_id != trip.city_id:
                raise ApiError(422, "unknown_place", "넣을 장소를 찾을 수 없어요")
    edited = apply_ops(day, await day_items(session, day), body.ops)
    adjust: AdjustResult | None = None
    final = edited
    if body.auto_adjust:
        adjust = auto_adjust(await engine_day(session, trip, day, edited), day.cur_index)
        if adjust.applied:
            session.add(
                ItinerarySnapshot(
                    trip_day_id=day.id,
                    items=items_json(edited),
                    reason="auto_adjust",
                    messages=list(adjust.messages),
                )
            )
            final = list(adjust.items)
    await save_items(session, day, final)
    day.version += 1
    await session.commit()
    return await render_day(session, trip, day, adjust)


@router.post("/{trip_id}/days/{day_index}/undo")
async def undo(trip_id: uuid.UUID, day_index: int, session: Session, user: CurrentUser) -> dict[str, Any]:
    """직전 자동 조정을 되돌린다(F-06)."""
    trip = await own_trip(session, user.id, trip_id)
    day = await get_day(session, trip, day_index, lock=True)
    snap = await undo_snapshot(session, day)
    if snap is None:
        raise ApiError(409, "nothing_to_undo", "되돌릴 자동 조정이 없어요")
    await save_items(session, day, _items_from(snap.items))
    await session.delete(snap)
    day.version += 1
    await session.commit()
    return await render_day(session, trip, day)


@router.post("/{trip_id}/days/{day_index}/suggest")
async def suggest(
    trip_id: uuid.UUID, day_index: int, body: SuggestIn, session: Session, user: CurrentUser
) -> dict[str, Any]:
    """추천 후보(F-07~F-09): replace(대체) / between(사이 들르기) / fill(남는 시간)."""
    trip = await own_trip(session, user.id, trip_id)
    day = await get_day(session, trip, day_index)
    items = await day_items(session, day)
    if body.mode in ("replace", "between") and body.position is None:
        raise ApiError(422, "invalid_request", "replace·between에는 position이 필요해요")
    pool_ids = await bridge.nearby_place_ids(session, trip.city_id, [uuid.UUID(it.place_id) for it in items])
    pool = list((await bridge.engine_places(session, pool_ids, day.date)).values())
    edays = await engine_day(session, trip, day, items)
    options = await anyio.to_thread.run_sync(
        lambda: engine_suggest(edays, body.mode, pool, position=body.position, cur=day.cur_index)
    )
    names = [edays.places[it.place_id].name for it in items]
    if body.mode == "replace":
        title = f"{names[body.position or 0]} 대신 갈 곳"
    elif body.mode == "between":
        pos = body.position or 0
        title = f"{names[pos - 1] if pos > 0 else '출발'} → {names[pos] if pos < len(names) else '도착'} 사이"
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
) -> dict[str, Any]:
    """도착 체크(F-11): 지금 있는 장소를 앞으로 옮긴다. 이후 자동 조정은 이 장소까지를 건드리지 않는다."""
    trip = await own_trip(session, user.id, trip_id)
    day = await get_day(session, trip, day_index, lock=True)
    items = await day_items(session, day)
    if not (day.cur_index < body.position < len(items)):
        raise ApiError(422, "invalid_position", "도착 체크할 수 없는 위치예요")
    day.cur_index = body.position
    await save_items(session, day, items)
    day.version += 1
    await session.commit()
    return await render_day(session, trip, day)

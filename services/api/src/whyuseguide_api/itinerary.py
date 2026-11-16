"""여행 일정 서비스: 하루 일정을 DB에서 읽고 쓰고, 엔진 입력으로 바꾸고, 응답 모양으로 만든다.

라우터(trips, ratings)가 같이 쓴다. 시간·순서·요금 계산은 일정 엔진과 timed(OTP 시간표 구간)가 한다.
"""

import enum
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itinerary_engine.adjust import AdjustResult
from itinerary_engine.compute import compute
from itinerary_engine.generate import FixedBooking, GeneratedDay, GenerateInput, Prefs
from itinerary_engine.models import Day as EngineDay
from itinerary_engine.models import DayResult, Item, LegRow, PlanMode, Point, StopRow
from whyuseguide_api import bridge, timed
from whyuseguide_api.db.models import City, ItineraryItem, ItinerarySnapshot, Trip, TripDay
from whyuseguide_api.errors import ApiError
from whyuseguide_api.schemas import hhmm, to_time

DEFAULT_PLAN_MODE: PlanMode = "balance"
LEG_LABELS = {"walk": "도보", "transit": "대중교통", "taxi": "택시", "car": "자동차"}


class SnapshotReason(enum.StrEnum):
    """itinerary_snapshots.reason."""

    generate = "generate"  # 일정 생성 때의 이동 방식별 순서
    auto_adjust = "auto_adjust"  # 자동 조정 직전 상태(되돌리기용)


@dataclass(frozen=True)
class Routing:
    """요청 하나에서 쓰는 시간표 구간 계산 수단. 없으면(None) 엔진 근사만 쓴다."""

    planner: timed.OtpPlanner
    transit: timed.CityTransit


async def routing_for(
    session: AsyncSession, planner: timed.OtpPlanner | None, city_id: uuid.UUID
) -> Routing | None:
    if planner is None:
        return None
    return Routing(planner, await timed.city_transit(session, city_id))


async def shown(eday: EngineDay, routing: Routing | None) -> DayResult:
    """화면에 보여 줄 계산: OTP 시간표 구간(열차에 맞춘 출발 시각), 실패한 구간만 근사(§5.3)."""
    if routing is None:
        return compute(eday)
    return await timed.timed_compute(eday, routing.planner, routing.transit)


# --- 응답 모양


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
                fixed_at=to_time(it.fixed_at) if it.fixed_at else None,
                status="done" if pos <= day.cur_index else "planned",
            )
        )


def items_json(items: list[Item] | tuple[Item, ...]) -> list[dict[str, Any]]:
    return [it.model_dump(by_alias=True) for it in items]


def items_from(data: list[dict[str, Any]]) -> list[Item]:
    return [Item.model_validate(x) for x in data]


async def lodging(session: AsyncSession, day: TripDay) -> tuple[Point | None, Point | None]:
    """그날의 시작·끝 지점(숙소). 없으면 None."""
    s_lat, s_lon, e_lat, e_lon = (
        await session.execute(
            select(
                func.ST_Y(func.geometry(TripDay.start_location)),
                func.ST_X(func.geometry(TripDay.start_location)),
                func.ST_Y(func.geometry(TripDay.end_location)),
                func.ST_X(func.geometry(TripDay.end_location)),
            ).where(TripDay.id == day.id)
        )
    ).one()
    return (
        Point(lat=s_lat, lon=s_lon) if s_lat is not None else None,
        Point(lat=e_lat, lon=e_lon) if e_lat is not None else None,
    )


async def engine_day(session: AsyncSession, trip: Trip, day: TripDay, items: list[Item]) -> EngineDay:
    places = await bridge.engine_places(session, [uuid.UUID(it.place_id) for it in items], day.date)
    start_point, end_point = await lodging(session, day)
    return EngineDay(
        date=day.date.isoformat(),
        start=hhmm(day.start_time),
        items=tuple(items),
        places=places,
        city=await bridge.city_network(session, trip.city_id),
        plan_mode=day.plan_mode,
        delay={"gap": day.delay_gap, "minutes": day.delay_min} if day.delay_gap is not None else None,
        start_point=start_point,
        end_point=end_point,
        day_end=hhmm(trip.day_end_time) if trip.day_end_time else None,
    )


# --- 스냅샷


async def latest_snapshot(
    session: AsyncSession, day: TripDay, reason: SnapshotReason, plan_mode: str | None = None
) -> ItinerarySnapshot | None:
    stmt = select(ItinerarySnapshot).where(
        ItinerarySnapshot.trip_day_id == day.id, ItinerarySnapshot.reason == reason
    )
    if plan_mode is not None:
        stmt = stmt.where(ItinerarySnapshot.plan_mode == plan_mode)
    return (
        await session.execute(stmt.order_by(ItinerarySnapshot.created_at.desc()).limit(1))
    ).scalar_one_or_none()


async def undo_snapshot(session: AsyncSession, day: TripDay) -> ItinerarySnapshot | None:
    return await latest_snapshot(session, day, SnapshotReason.auto_adjust)


def ordered_items(gday: GeneratedDay, mode: PlanMode) -> list[Item]:
    """생성 결과에서 그 이동 방식 안의 방문 순서대로 놓은 일정 항목."""
    by_id = {it.place_id: it for it in gday.items}
    return [by_id[r.place_id] for r in gday.plans[mode].rows if isinstance(r, StopRow)]


async def store_generated(
    session: AsyncSession, day: TripDay, gday: GeneratedDay
) -> dict[PlanMode, list[Item]]:
    """생성 결과의 이동 방식별 순서를 스냅샷으로 저장한다. 그날의 예전 스냅샷은 모두 지운다.

    예전 자동 조정 스냅샷이 남으면 "되돌리기"가 다시 만들기 전 일정을 되살린다.
    """
    await session.execute(delete(ItinerarySnapshot).where(ItinerarySnapshot.trip_day_id == day.id))
    ordered = {mode: ordered_items(gday, mode) for mode in gday.plans}
    for mode, items in ordered.items():
        session.add(
            ItinerarySnapshot(
                trip_day_id=day.id,
                items=items_json(items),
                reason=SnapshotReason.generate,
                plan_mode=mode,
            )
        )
    return ordered


# --- 일정 생성 입력


async def generate_input(
    session: AsyncSession,
    city: City,
    dates: list[date],
    place_ids: list[uuid.UUID],
    *,
    tastes: list[str],
    start: str,
    pace: str,
    mix: int,
    fixed: tuple[FixedBooking, ...],
    start_point: Point | None,
    end_point: Point | None,
    day_end: str | None,
    prefs: Prefs | None = None,
) -> GenerateInput:
    """일정 생성(§5.2) 입력. 여행을 처음 만들 때와 평가 뒤 다음 날을 다시 만들 때 같이 쓴다."""
    extra: dict[str, Any] = {"prefs": prefs} if prefs is not None else {}
    return GenerateInput(
        dates=tuple(d.isoformat() for d in dates),
        tastes=tuple(tastes),
        places={d.isoformat(): await bridge.engine_places(session, place_ids, d) for d in dates},
        profiles=await bridge.profiles(session, place_ids),
        start=start,
        pace=pace,
        mix=mix,
        fixed=fixed,
        city=await bridge.city_network(session, city.id),
        city_move_cap_factor=float(city.move_cap_factor),
        start_point=start_point,
        end_point=end_point,
        day_end=day_end,
        **extra,
    )


# --- 응답 만들기


async def baseline_for(
    session: AsyncSession, routing: Routing | None, trip: Trip, day: TripDay
) -> DayResult | None:
    """처음 생성한 일정(그날 이동 방식)의 결과. S-03 "처음 코스와 비교"."""
    snap = await latest_snapshot(session, day, SnapshotReason.generate, day.plan_mode)
    if snap is None:
        return None
    return await shown(await engine_day(session, trip, day, items_from(snap.items)), routing)


async def render_day(
    session: AsyncSession,
    routing: Routing | None,
    trip: Trip,
    day: TripDay,
    adjust: AdjustResult | None = None,
) -> dict[str, Any]:
    items = await day_items(session, day)
    result = await shown(await engine_day(session, trip, day, items), routing)
    return day_payload(
        day,
        result,
        adjust=adjust,
        undo_available=await undo_snapshot(session, day) is not None,
        baseline=await baseline_for(session, routing, trip, day),
    )


async def trip_payload(session: AsyncSession, routing: Routing | None, trip: Trip) -> dict[str, Any]:
    days = (
        await session.execute(select(TripDay).where(TripDay.trip_id == trip.id).order_by(TripDay.day_index))
    ).scalars()
    return {
        "tripId": str(trip.id),
        "startDate": trip.start_date.isoformat(),
        "days": [await render_day(session, routing, trip, d) for d in days],
        "planMode": trip.plan_mode,
        "pace": trip.pace,
        "tastes": list(trip.tastes),
    }

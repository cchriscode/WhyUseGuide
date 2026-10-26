"""코스 평가 (PRD §7 POST /course-ratings, F-24·F-25, §5.6)."""

import uuid
from typing import Any, Literal

import anyio
from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from itinerary_engine.feedback import CourseRating as EngineRating
from itinerary_engine.feedback import apply_rating
from itinerary_engine.generate import FixedBooking, GenerateInput, Prefs, generate
from itinerary_engine.models import PlanMode, StopRow
from whyuseguide_api import bridge
from whyuseguide_api.db.models import (
    City,
    CoursePlaceVote,
    CourseRating,
    ItineraryItem,
    ItinerarySnapshot,
    Place,
    Trip,
    TripDay,
    TripFixedBooking,
)
from whyuseguide_api.deps import CurrentUser, Session
from whyuseguide_api.errors import ApiError
from whyuseguide_api.routers.trips import (
    Model,
    day_items,
    get_day,
    hhmm,
    items_json,
    own_trip,
    render_day,
    save_items,
)

router = APIRouter(prefix="/v1/course-ratings", tags=["ratings"])


class RatingIn(Model):
    trip_id: uuid.UUID
    day_index: int = Field(ge=0)
    stars: int = Field(ge=1, le=5)
    tags: list[str] = []
    votes: dict[uuid.UUID, Literal[1, -1]] = {}
    route_accuracy: Literal["ok", "bit", "off"] | None = None
    apply_next: bool = True
    share_anon: bool = True


@router.post("", status_code=201)
async def rate(body: RatingIn, session: Session, user: CurrentUser) -> dict[str, Any]:
    trip = await own_trip(session, user.id, body.trip_id)
    day = await get_day(session, trip, body.day_index)
    items = await day_items(session, day)
    in_day = {uuid.UUID(it.place_id) for it in items}
    if set(body.votes) - in_day:
        raise ApiError(422, "invalid_vote", "그날 일정에 없는 장소에는 좋아요·별로를 남길 수 없어요")

    rating = CourseRating(
        trip_day_id=day.id,
        user_id=user.id,
        stars=body.stars,
        tags=body.tags,
        route_accuracy=body.route_accuracy,
        apply_next=body.apply_next,
        share_anon=body.share_anon,
    )
    session.add(rating)
    await session.flush()
    for pid, vote in body.votes.items():
        session.add(CoursePlaceVote(course_rating_id=rating.id, place_id=pid, vote=vote))

    profiles = await bridge.profiles(session, list(in_day))
    types = [t for (t,) in (await session.execute(select(Place.type).where(Place.id.in_(in_day)))).all()]
    feedback = apply_rating(
        Prefs.model_validate(trip.prefs or {}),
        EngineRating(
            stars=body.stars,
            tags=tuple(body.tags),
            votes={str(k): v for k, v in body.votes.items()},
            route_accuracy=body.route_accuracy,
        ),
        profiles,
        types,
    )

    next_day_payload = None
    if body.apply_next:
        trip.prefs = feedback.prefs.model_dump()
        next_day = (
            await session.execute(
                select(TripDay).where(TripDay.trip_id == trip.id, TripDay.day_index == body.day_index + 1)
            )
        ).scalar_one_or_none()
        if next_day is not None and next_day.cur_index < 0:
            await regenerate_day(session, trip, next_day, feedback.prefs)
            await session.commit()
            next_day_payload = await render_day(session, trip, next_day)
    await session.commit()
    return {
        "messages": list(feedback.messages),
        "prefs": feedback.prefs.model_dump(by_alias=True),
        "verifyRoutes": feedback.verify_routes,
        "nextDay": next_day_payload,
    }


async def regenerate_day(session: AsyncSession, trip: Trip, day: TripDay, prefs: Prefs) -> None:
    """평가를 반영해 그날 일정을 다시 만든다. 다른 날 일정에 들어간 장소는 다시 넣지 않는다."""
    trip_id = trip.id
    city = await session.get(City, trip.city_id)
    if city is None:
        raise ApiError(404, "not_found", "도시를 찾을 수 없어요")
    used = set(
        (
            await session.execute(
                select(ItineraryItem.place_id)
                .join(TripDay, TripDay.id == ItineraryItem.trip_day_id)
                .where(TripDay.trip_id == trip_id, TripDay.id != day.id)
            )
        ).scalars()
    )
    fixed = (
        (
            await session.execute(
                select(TripFixedBooking).where(
                    TripFixedBooking.trip_id == trip_id, TripFixedBooking.date == day.date
                )
            )
        )
        .scalars()
        .all()
    )
    ids = [pid for pid in await bridge.active_place_ids(session, trip.city_id) if pid not in used]
    gen = GenerateInput(
        dates=(day.date.isoformat(),),
        tastes=tuple(trip.tastes),
        places={day.date.isoformat(): await bridge.engine_places(session, ids, day.date)},
        profiles=await bridge.profiles(session, ids),
        start=hhmm(day.start_time),
        pace=trip.pace,
        mix=trip.mix,
        fixed=tuple(
            FixedBooking(place_id=str(f.place_id), date=day.date.isoformat(), time=hhmm(f.time))
            for f in fixed
        ),
        city=await bridge.city_network(session, trip.city_id),
        city_move_cap_factor=float(city.move_cap_factor),
        day_end=hhmm(trip.day_end_time) if trip.day_end_time else None,
        prefs=prefs,
    )
    result = await anyio.to_thread.run_sync(generate, gen)
    gday = result.days[0]
    mode: PlanMode = day.plan_mode if day.plan_mode in gday.plans else "balance"
    mode_plan = gday.plans[mode]
    order = [r.place_id for r in mode_plan.rows if isinstance(r, StopRow)]
    by_id = {it.place_id: it for it in gday.items}
    await save_items(session, day, [by_id[p] for p in order])
    await session.execute(
        delete(ItinerarySnapshot).where(
            ItinerarySnapshot.trip_day_id == day.id, ItinerarySnapshot.reason == "generate"
        )
    )
    for mode, plan in gday.plans.items():
        mode_order = [r.place_id for r in plan.rows if isinstance(r, StopRow)]
        session.add(
            ItinerarySnapshot(
                trip_day_id=day.id,
                items=items_json([by_id[p] for p in mode_order]),
                reason="generate",
                plan_mode=mode,
            )
        )
    day.version += 1

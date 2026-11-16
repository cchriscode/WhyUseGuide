"""코스 평가 (PRD §7 POST /course-ratings, F-24·F-25, §5.6)."""

import uuid
from typing import Any, Literal

import anyio
from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from itinerary_engine.feedback import CourseRating as EngineRating
from itinerary_engine.feedback import RatingTag, RouteAccuracy, apply_rating
from itinerary_engine.generate import FixedBooking, Prefs, generate
from itinerary_engine.models import PlanMode
from whyuseguide_api import bridge, itinerary
from whyuseguide_api.db.models import (
    City,
    CoursePlaceVote,
    CourseRating,
    ItineraryItem,
    Place,
    Trip,
    TripDay,
    TripFixedBooking,
)
from whyuseguide_api.deps import CurrentUser, Planner, Session
from whyuseguide_api.errors import ApiError
from whyuseguide_api.itinerary import DEFAULT_PLAN_MODE
from whyuseguide_api.schemas import ApiModel, hhmm

router = APIRouter(prefix="/v1/course-ratings", tags=["ratings"])


class RatingIn(ApiModel):
    trip_id: uuid.UUID
    day_index: int = Field(ge=0)
    stars: int = Field(ge=1, le=5)
    tags: list[RatingTag] = []  # 코드값(엔진 feedback.RatingTag)
    votes: dict[uuid.UUID, Literal[1, -1]] = {}
    route_accuracy: RouteAccuracy | None = None
    apply_next: bool = True
    share_anon: bool = True


def already_rated() -> ApiError:
    return ApiError(409, "already_rated", "이 날은 이미 평가했어요")


@router.post("", status_code=201)
async def rate(body: RatingIn, session: Session, user: CurrentUser, planner: Planner) -> dict[str, Any]:
    trip = await itinerary.own_trip(session, user.id, body.trip_id)
    day = await itinerary.get_day(session, trip, body.day_index)
    items = await itinerary.day_items(session, day)
    in_day = {uuid.UUID(it.place_id) for it in items}
    if set(body.votes) - in_day:
        raise ApiError(422, "invalid_vote", "그날 일정에 없는 장소에는 좋아요·별로를 남길 수 없어요")
    # 하루에 평가 하나: 다시 보내면 반영(§5.6)이 두 번 된다. 동시에 들어온 요청은 DB 유일 제약이 막는다.
    already = await session.execute(
        select(CourseRating.id).where(CourseRating.trip_day_id == day.id, CourseRating.user_id == user.id)
    )
    if already.first() is not None:
        raise already_rated()

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
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        raise already_rated() from e
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
            routing = await itinerary.routing_for(session, planner, trip.city_id)
            next_day_payload = await itinerary.render_day(session, routing, trip, next_day)
    await session.commit()
    return {
        "messages": list(feedback.messages),
        "prefs": feedback.prefs.model_dump(by_alias=True),
        "verifyRoutes": feedback.verify_routes,
        "nextDay": next_day_payload,
    }


async def regenerate_day(session: AsyncSession, trip: Trip, day: TripDay, prefs: Prefs) -> None:
    """평가를 반영해 그날 일정을 다시 만든다. 다른 날 일정에 들어간 장소는 다시 넣지 않는다."""
    city = await session.get(City, trip.city_id)
    if city is None:
        raise ApiError(404, "not_found", "도시를 찾을 수 없어요")
    used = set(
        (
            await session.execute(
                select(ItineraryItem.place_id)
                .join(TripDay, TripDay.id == ItineraryItem.trip_day_id)
                .where(TripDay.trip_id == trip.id, TripDay.id != day.id)
            )
        ).scalars()
    )
    fixed = (
        (
            await session.execute(
                select(TripFixedBooking).where(
                    TripFixedBooking.trip_id == trip.id, TripFixedBooking.date == day.date
                )
            )
        )
        .scalars()
        .all()
    )
    ids = [pid for pid in await bridge.active_place_ids(session, trip.city_id) if pid not in used]
    start_point, end_point = await itinerary.lodging(session, day)
    gen = await itinerary.generate_input(
        session,
        city,
        [day.date],
        ids,
        tastes=list(trip.tastes),
        start=hhmm(day.start_time),
        pace=trip.pace,
        mix=trip.mix,
        fixed=tuple(
            FixedBooking(place_id=str(f.place_id), date=day.date.isoformat(), time=hhmm(f.time))
            for f in fixed
        ),
        start_point=start_point,
        end_point=end_point,
        day_end=hhmm(trip.day_end_time) if trip.day_end_time else None,
        prefs=prefs,
    )
    gday = (await anyio.to_thread.run_sync(generate, gen)).days[0]
    ordered = await itinerary.store_generated(session, day, gday)
    mode: PlanMode = next((m for m in ordered if m == day.plan_mode), DEFAULT_PLAN_MODE)
    await itinerary.save_items(session, day, ordered[mode])
    day.version += 1

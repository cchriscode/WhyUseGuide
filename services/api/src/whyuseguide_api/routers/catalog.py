"""도시·장소 조회 (PRD §7: GET /cities, /places, /places/{id}). 공개 정보라 토큰 없이 쓴다."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from geoalchemy2.functions import ST_X, ST_Y, ST_Distance, ST_GeogFromText
from sqlalchemy import cast, func, or_, select
from sqlalchemy.types import Float

from whyuseguide_api.db.models import City, Place, PlaceHours, PlaceStatus, Stop
from whyuseguide_api.deps import Session
from whyuseguide_api.errors import ApiError
from whyuseguide_api.schemas import ApiModel, hhmm

router = APIRouter(prefix="/v1", tags=["catalog"])


class CityOut(ApiModel):
    code: str
    name_ko: str
    timezone: str
    currency: str
    launched: bool
    data_tier: str
    kind: str


class PlaceSummary(ApiModel):
    id: str
    name_ko: str | None
    name_local: str
    type: str
    rating: float | None
    popularity: int
    lat: float
    lon: float
    distance_m: int | None = None


class HoursOut(ApiModel):
    weekday: int  # 0 = 월요일
    open: str
    close: str
    last_entry: str | None


class StopOut(ApiModel):
    id: str
    name_ko: str | None
    name_local: str
    code: str | None


class PlaceDetail(PlaceSummary):
    name_en: str | None
    indoor: bool
    default_stay_min: int
    cost_per_person: int
    currency: str
    bookable: bool
    tip_ko: str | None
    website: str | None
    verified_at: datetime | None
    hours: list[HoursOut]
    nearest_stop: StopOut | None


@router.get("/cities", response_model=list[CityOut], response_model_by_alias=True)
async def cities(session: Session) -> list[CityOut]:
    rows = (await session.execute(select(City).order_by(City.code))).scalars()
    return [
        CityOut(
            code=c.code,
            name_ko=c.name_ko,
            timezone=c.timezone,
            currency=c.currency,
            launched=c.launched,
            data_tier=c.data_tier.value,
            kind=c.kind.value,
        )
        for c in rows
    ]


@router.get("/places", response_model=list[PlaceSummary], response_model_by_alias=True)
async def places(
    session: Session,
    city: str,
    q: str | None = None,
    type: str | None = None,
    near: Annotated[
        str | None, Query(pattern=r"^-?\d+(\.\d+)?,-?\d+(\.\d+)?$", description="lat,lon")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[PlaceSummary]:
    """장소 검색(F-01, F-05). 검수된(active) 장소만 나온다."""
    stmt = (
        select(
            Place,
            cast(ST_Y(func.geometry(Place.location)), Float),
            cast(ST_X(func.geometry(Place.location)), Float),
        )
        .join(City, City.id == Place.city_id)
        .where(City.code == city, Place.status == PlaceStatus.active)
    )
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(Place.name_ko.ilike(pattern), Place.name_local.ilike(pattern), Place.name_en.ilike(pattern))
        )
    if type:
        stmt = stmt.where(Place.type == type)
    distance = None
    if near:
        lat, lon = (float(x) for x in near.split(","))
        distance = ST_Distance(Place.location, ST_GeogFromText(f"SRID=4326;POINT({lon} {lat})"))
        stmt = stmt.add_columns(distance).order_by(distance)
    else:
        stmt = stmt.order_by(Place.popularity.desc(), Place.rating.desc().nulls_last(), Place.name_local)
    rows = (await session.execute(stmt.limit(limit))).all()
    return [
        PlaceSummary(
            id=str(r[0].id),
            name_ko=r[0].name_ko,
            name_local=r[0].name_local,
            type=r[0].type,
            rating=float(r[0].rating) if r[0].rating is not None else None,
            popularity=r[0].popularity,
            lat=r[1],
            lon=r[2],
            distance_m=round(r[3]) if distance is not None else None,
        )
        for r in rows
    ]


@router.get("/places/{place_id}", response_model=PlaceDetail, response_model_by_alias=True)
async def place_detail(session: Session, place_id: uuid.UUID) -> PlaceDetail:
    """장소 상세(F-16, F-19): 영업시간, 팁, 가까운 역, 확인일."""
    row = (
        await session.execute(
            select(
                Place,
                cast(ST_Y(func.geometry(Place.location)), Float),
                cast(ST_X(func.geometry(Place.location)), Float),
            ).where(Place.id == place_id, Place.status == PlaceStatus.active)
        )
    ).one_or_none()
    if row is None:
        raise ApiError(404, "not_found", "장소를 찾을 수 없어요")
    p, lat, lon = row
    hours = (
        await session.execute(
            select(PlaceHours)
            .where(PlaceHours.place_id == p.id)
            .order_by(PlaceHours.weekday, PlaceHours.open_time)
        )
    ).scalars()
    stop = await session.get(Stop, p.nearest_stop_id) if p.nearest_stop_id else None
    return PlaceDetail(
        id=str(p.id),
        name_ko=p.name_ko,
        name_local=p.name_local,
        name_en=p.name_en,
        type=p.type,
        rating=float(p.rating) if p.rating is not None else None,
        popularity=p.popularity,
        lat=lat,
        lon=lon,
        indoor=p.indoor,
        default_stay_min=p.default_stay_min,
        cost_per_person=p.cost_per_person,
        currency=p.currency,
        bookable=p.bookable,
        tip_ko=p.tip_ko,
        website=p.website,
        verified_at=p.verified_at,
        hours=[
            HoursOut(
                weekday=h.weekday,
                open=hhmm(h.open_time),
                close=hhmm(h.close_time),
                last_entry=hhmm(h.last_entry_time) if h.last_entry_time else None,
            )
            for h in hours
        ],
        nearest_stop=StopOut(
            id=str(stop.id), name_ko=stop.name_ko, name_local=stop.name_local, code=stop.code
        )
        if stop
        else None,
    )

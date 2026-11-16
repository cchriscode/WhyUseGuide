"""DB ↔ 일정 엔진 변환.

엔진은 날짜를 모른다. 여기서 그날의 영업시간·휴무를 정리한 장소, 도시 노선 정보, 추천용 태그를 만들어 넘긴다.
"""

import logging
import uuid
from collections import defaultdict
from datetime import date, time
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from itinerary_engine.generate import Profile
from itinerary_engine.models import City as EngineCity
from itinerary_engine.models import Place as EnginePlace
from whyuseguide_api import network
from whyuseguide_api.db.models import City, Place, PlaceClosure, PlaceHours, PlaceStatus, PlaceTag


def hhmm(t: time | None) -> str | None:
    return t.strftime("%H:%M") if t else None


log = logging.getLogger(__name__)
_warned_no_fare: set[uuid.UUID] = set()


async def city_network(session: AsyncSession, city_id: uuid.UUID) -> EngineCity:
    rows = await session.execute(network.LINE_LINKS_SQL, network.line_links_params(city_id))
    fare: dict[str, Any] = (
        await session.execute(select(City.fare_table).where(City.id == city_id))
    ).scalar_one() or {}
    city = EngineCity.model_validate({"lineLinks": network.line_links(rows), **fare})
    if not (city.fare_bands and city.taxi) and city_id not in _warned_no_fare:
        # 운임표가 없으면 엔진은 대중교통 요금을 0으로 계산하고 택시를 후보에서 뺀다.
        # 조용히 지나가지 않게 도시마다 한 번 알린다(채우기: uv run wug-data load-fares).
        _warned_no_fare.add(city_id)
        log.warning(
            "cities.fare_table이 덜 찼다(대중교통 운임 %d구간, 택시 %s): 교통비가 틀리게 나온다 (city %s)",
            len(city.fare_bands),
            "있음" if city.taxi else "없음",
            city_id,
        )
    return city


async def place_stations(
    session: AsyncSession, place_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, float]]:
    """장소별 {노선 id: 그 노선의 가장 가까운 역까지 km}. 역이 없는 장소는 빠진다(엔진은 빈 값 = 역이 멂)."""
    if not place_ids:
        return {}
    rows = await session.execute(network.PLACE_STATIONS_SQL, network.place_stations_params(place_ids))
    return network.stations_by_place(rows)


async def engine_places(
    session: AsyncSession, place_ids: list[uuid.UUID], on: date
) -> dict[str, EnginePlace]:
    """그날 기준 장소. 영업시간 행이 하나도 없으면 시간 제약 없음, 다른 요일만 있으면 그날 휴무."""
    if not place_ids:
        return {}
    places = (await session.execute(select(Place).where(Place.id.in_(place_ids)))).scalars().all()
    coords = {
        r.id: (r.lat, r.lon)
        for r in await session.execute(
            text(
                "SELECT id, ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lon"
                " FROM places WHERE id = ANY(:ids)"
            ),
            {"ids": place_ids},
        )
    }
    hours: dict[uuid.UUID, list[PlaceHours]] = defaultdict(list)
    for h in (await session.execute(select(PlaceHours).where(PlaceHours.place_id.in_(place_ids)))).scalars():
        hours[h.place_id].append(h)
    closed_on = set(
        (
            await session.execute(
                select(PlaceClosure.place_id).where(
                    PlaceClosure.place_id.in_(place_ids), PlaceClosure.date == on
                )
            )
        ).scalars()
    )
    stations = await place_stations(session, place_ids)
    out: dict[str, EnginePlace] = {}
    for p in places:
        today = [h for h in hours[p.id] if h.weekday == on.weekday()]
        open_t = min((h.open_time for h in today), default=None)
        close_t = max((h.close_time for h in today), default=None)
        last = max((h.last_entry_time for h in today if h.last_entry_time), default=None)
        closed = p.id in closed_on or p.status == PlaceStatus.closed or (bool(hours[p.id]) and not today)
        lat, lon = coords[p.id]
        out[str(p.id)] = EnginePlace(
            id=str(p.id),
            name=p.name_ko or p.name_local,
            type=p.type,
            lat=lat,
            lon=lon,
            default_stay_min=p.default_stay_min,
            rating=float(p.rating) if p.rating is not None else 0.0,
            cost_per_person=p.cost_per_person,
            open=hhmm(open_t),
            close=hhmm(close_t),
            last_entry=hhmm(last),
            closed=closed,
            indoor=p.indoor,
            parking_fee=p.parking_fee or 0,
            stations=dict(sorted(stations.get(p.id, {}).items())),
        )
    return out


async def active_place_ids(session: AsyncSession, city_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await session.execute(
                select(Place.id).where(Place.city_id == city_id, Place.status == PlaceStatus.active)
            )
        ).scalars()
    )


async def profiles(session: AsyncSession, place_ids: list[uuid.UUID]) -> dict[str, Profile]:
    tags: dict[uuid.UUID, dict[str, float]] = defaultdict(dict)
    for t in (await session.execute(select(PlaceTag).where(PlaceTag.place_id.in_(place_ids)))).scalars():
        # 출처(llm·editor·user)가 여럿이면 큰 값을 쓴다
        tags[t.place_id][t.tag] = max(tags[t.place_id].get(t.tag, 0.0), float(t.weight))
    pops = {
        pid: pop
        for pid, pop in await session.execute(
            select(Place.id, Place.popularity).where(Place.id.in_(place_ids))
        )
    }
    return {str(pid): Profile(tags=tags.get(pid, {}), popularity=pops.get(pid, 3)) for pid in place_ids}


async def nearby_place_ids(
    session: AsyncSession, city_id: uuid.UUID, around: list[uuid.UUID], radius_m: int = 2000, limit: int = 40
) -> list[uuid.UUID]:
    """추천 후보 풀(§5.5): 그날 장소들 반경 안의 검수된 장소, 가까운 순."""
    if not around:
        return []
    rows = await session.execute(
        text(
            """
            SELECT p.id, min(ST_Distance(p.location, d.location)) AS dist
            FROM places p JOIN places d ON d.id = ANY(:around) AND ST_DWithin(p.location, d.location, :r)
            WHERE p.city_id = :city AND p.status = 'active'
            GROUP BY p.id ORDER BY dist, p.id LIMIT :limit
            """
        ),
        {"around": around, "r": radius_m, "city": city_id, "limit": limit},
    )
    return [r.id for r in rows]

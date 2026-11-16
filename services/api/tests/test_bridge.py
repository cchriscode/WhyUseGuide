import uuid

import anyio
from conftest import DB_URL, needs_db, place_id
from sqlalchemy import Connection, Engine, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from whyuseguide_api.bridge import place_stations
from whyuseguide_api.db.models import City, Line, Stop, StopLine

pytestmark = needs_db

SOURCE = "test-bridge"


def add_stop(
    conn: Connection, city_id: uuid.UUID, line_id: uuid.UUID, sid: str, lat: float, lon: float
) -> None:
    stop_id = conn.execute(
        insert(Stop)
        .values(
            city_id=city_id,
            source_id=SOURCE,
            gtfs_stop_id=sid,
            name_local=sid,
            location=func.ST_GeogFromText(f"SRID=4326;POINT({lon} {lat})"),
        )
        .returning(Stop.id)
    ).scalar_one()
    conn.execute(insert(StopLine).values(stop_id=stop_id, line_id=line_id))


def test_place_stations_gives_nearest_station_per_line(db: Engine) -> None:
    """도쿄 국립박물관(35.7188, 139.7765) 기준: 노선별로 가장 가까운 역, 800m 밖 역은 뺀다."""
    tnm = uuid.UUID(place_id(db, "tnm"))
    with db.begin() as conn:
        city_id = conn.execute(select(City.id).where(City.code == "tokyo")).scalar_one()
        lines = {}
        for rid in ("A", "B"):
            lines[rid] = conn.execute(
                insert(Line)
                .values(city_id=city_id, source_id=SOURCE, gtfs_route_id=rid, name_local=rid, operator="test")
                .returning(Line.id)
            ).scalar_one()
        add_stop(conn, city_id, lines["A"], "a-near", 35.7206, 139.7765)  # 북쪽 약 200m
        add_stop(conn, city_id, lines["A"], "a-far", 35.7233, 139.7765)  # 약 500m (같은 노선, 더 멂)
        add_stop(conn, city_id, lines["B"], "b-out", 35.7278, 139.7765)  # 약 1km → 제외

    async def fetch() -> dict[uuid.UUID, dict[str, float]]:
        assert DB_URL
        engine = create_async_engine(DB_URL)
        async with AsyncSession(engine) as session:
            out = await place_stations(session, [tnm])
        await engine.dispose()
        return out

    try:
        stations = anyio.run(fetch)[tnm]
        assert set(stations) == {str(lines["A"])}
        assert abs(stations[str(lines["A"])] - 0.2) < 0.005
    finally:
        with db.begin() as conn:
            conn.execute(delete(Stop).where(Stop.source_id == SOURCE))
            conn.execute(delete(Line).where(Line.source_id == SOURCE))


def test_city_network_is_kept_in_memory_until_cleared(db: Engine) -> None:
    """환승 노선 쌍·운임표는 느린 조회라 메모리에 둔다. 버리기 전에는 DB를 바꿔도 예전 값이다."""
    from whyuseguide_api import bridge

    with db.begin() as conn:
        city_id, before = conn.execute(select(City.id, City.fare_table).where(City.code == "tokyo")).one()

    async def fare_bands() -> int:
        assert DB_URL
        engine = create_async_engine(DB_URL)
        async with AsyncSession(engine) as session:
            city = await bridge.city_network(session, city_id)
        await engine.dispose()
        return len(city.fare_bands)

    bridge.clear_network_cache()
    try:
        first = anyio.run(fare_bands)
        with db.begin() as conn:
            conn.execute(
                update(City)
                .where(City.id == city_id)
                .values(fare_table={"fareBands": [{"maxKm": 99, "fare": 1}] * (first + 1)})
            )
        assert anyio.run(fare_bands) == first  # 아직 메모리의 값
        bridge.clear_network_cache()
        assert anyio.run(fare_bands) == first + 1
    finally:
        with db.begin() as conn:
            conn.execute(update(City).where(City.id == city_id).values(fare_table=before))
        bridge.clear_network_cache()

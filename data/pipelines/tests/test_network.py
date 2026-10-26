import os
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine, select, update

from whyuseguide_api.db.models import Line, NameSource, Stop, StopLine
from whyuseguide_pipelines.gtfs import GtfsLine, GtfsStop, Network, read_network
from whyuseguide_pipelines.load import KoreanNames, apply_korean_names, load_network

FEED = {
    "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\ntoei,東京都交通局,https://x,Asia/Tokyo\n",
    "routes.txt": "route_id,agency_id,route_long_name,route_type,route_color\n"
    "1,toei,浅草線,1,ff535f\n2,toei,荒川線,0,\n",
    "stops.txt": "stop_id,stop_code,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
    "S1,,浅草,35.711,139.797,1,\n"
    "101,A-18,浅草,35.7107,139.7966,0,S1\n"
    "102,A-17,蔵前,35.7031,139.7907,0,\n"
    "E1,,浅草駅 A4出口,35.7110,139.7970,2,S1\n"
    "201,,三ノ輪橋,35.7322,139.7914,0,\n",
    "trips.txt": "route_id,service_id,trip_id\n1,0,t1\n2,0,t2\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
    "t1,09:00:00,09:00:00,101,1\nt1,09:02:00,09:02:00,102,2\nt2,09:00:00,09:00:00,201,1\n",
    "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
    "start_date,end_date\n0,1,1,1,1,1,0,0,20260314,20270312\n",
    # 역 이름은 field_value 방식, 노선 이름은 record_id 방식
    "translations.txt": "table_name,field_name,language,translation,record_id,field_value\n"
    "stops,stop_name,en,Asakusa,,浅草\n"
    "stops,stop_name,ja,浅草,,浅草\n"
    "routes,route_long_name,en,Asakusa Line,1,\n",
}


def make_feed(tmp_path: Path, files: dict[str, str]) -> Path:
    path = tmp_path / "feed.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return path


def test_read_network(tmp_path: Path) -> None:
    net = read_network(make_feed(tmp_path, FEED))

    lines = {ln.route_id: ln for ln in net.lines}
    assert lines["1"].name_en == "Asakusa Line"
    assert lines["1"].color_hex == "#FF535F"
    assert lines["2"].color_hex is None
    assert lines["1"].operator == "東京都交通局"

    stops = {s.stop_id: s for s in net.stops}
    assert set(stops) == {"S1", "101", "102", "201"}  # 출입구(location_type 2)는 제외
    assert stops["101"].code == "A-18"
    assert stops["101"].name_en == "Asakusa"
    assert stops["101"].parent_stop_id == "S1"
    assert stops["102"].name_en is None

    # 역별 노선은 실제 운행에서 뽑으므로 운행이 없는 역(S1)은 노선이 없다.
    assert net.stop_lines == {("101", "1"), ("102", "1"), ("201", "2")}


# --- DB 적재: PostGIS가 필요하다. CI는 PostGIS 서비스와 TEST_DATABASE_URL로 실행한다.

DB_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(
    not DB_URL, reason="TEST_DATABASE_URL이 없으면 DB 적재 테스트는 실행하지 않는다"
)


@pytest.fixture
def conn() -> Iterator[Connection]:
    assert DB_URL
    engine = create_engine(DB_URL)
    with engine.connect() as c:
        tx = c.begin()
        yield c
        tx.rollback()
    engine.dispose()


def network(*stops: tuple[str, str], line_name: str = "浅草線") -> Network:
    return Network(
        lines=[GtfsLine("1", line_name, None, "#FF535F", "東京都交通局")],
        stops=[GtfsStop(sid, None, name, None, 35.7, 139.79, None) for sid, name in stops],
        stop_lines={(sid, "1") for sid, _ in stops},
    )


def rows(conn: Connection, source: str) -> tuple[dict[str, str], dict[str, str], int]:
    stops = {
        k: v
        for k, v in conn.execute(select(Stop.gtfs_stop_id, Stop.name_local).where(Stop.source_id == source))
    }
    lines = {
        k: v
        for k, v in conn.execute(select(Line.gtfs_route_id, Line.name_local).where(Line.source_id == source))
    }
    links = len(
        conn.execute(
            select(StopLine.id).join(Line, Line.id == StopLine.line_id).where(Line.source_id == source)
        ).all()
    )
    return stops, lines, links


@needs_db
def test_load_inserts_then_syncs(conn: Connection) -> None:
    first = load_network(conn, "test-feed", "tokyo", network(("101", "浅草"), ("102", "蔵前")))
    assert (first.lines, first.stops, first.stop_lines) == (1, 2, 2)

    second = load_network(
        conn, "test-feed", "tokyo", network(("101", "浅草"), ("103", "浅草橋"), line_name="都営浅草線")
    )
    assert (second.removed_stops, second.removed_lines) == (1, 0)
    stops, lines, links = rows(conn, "test-feed")
    assert stops == {"101": "浅草", "103": "浅草橋"}
    assert lines == {"1": "都営浅草線"}
    assert links == 2


@needs_db
def test_load_leaves_other_sources_alone(conn: Connection) -> None:
    load_network(conn, "test-a", "tokyo", network(("101", "浅草")))
    load_network(conn, "test-b", "tokyo", network(("101", "別の駅")))
    load_network(conn, "test-a", "tokyo", network(("102", "蔵前")))
    assert rows(conn, "test-b") == ({"101": "別の駅"}, {"1": "浅草線"}, 1)
    assert rows(conn, "test-a")[0] == {"102": "蔵前"}


@needs_db
def test_korean_names(conn: Connection) -> None:
    net = Network(
        lines=[GtfsLine("1", "浅草線", "Asakusa Line", None, "東京都交通局")],
        stops=[
            GtfsStop("101", None, "浅草", "Asakusa", 35.7, 139.79, None),
            GtfsStop("102", None, "蔵前", "Kuramae", 35.7, 139.79, None),
            GtfsStop("103", None, "押上", "Oshiage", 35.7, 139.79, None),
            GtfsStop("104", None, "謎", None, 35.7, 139.79, None),
        ],
        stop_lines=set(),
    )
    load_network(conn, "test-ko", "tokyo", net)
    conn.execute(
        update(Stop)
        .where(Stop.source_id == "test-ko", Stop.gtfs_stop_id == "103")
        .values(name_ko="오시아게(스카이트리)", name_ko_source=NameSource.official)
    )

    edited = KoreanNames(lines={}, stops={"蔵前": "쿠라마에"})
    result = apply_korean_names(conn, "test-ko", edited)
    assert (result.editor, result.machine, result.official, result.missing) == (1, 2, 1, 1)

    def names() -> dict[str, tuple[str | None, NameSource | None]]:
        q = select(Stop.gtfs_stop_id, Stop.name_ko, Stop.name_ko_source).where(Stop.source_id == "test-ko")
        return {sid: (ko, src) for sid, ko, src in conn.execute(q)}

    assert names() == {
        "101": ("아사쿠사", NameSource.machine),
        "102": ("쿠라마에", NameSource.editor),
        "103": ("오시아게(스카이트리)", NameSource.official),
        "104": (None, None),
    }
    line_ko = conn.execute(select(Line.name_ko).where(Line.source_id == "test-ko")).scalar_one()
    assert line_ko == "아사쿠사선"

    aliases = {
        sid: list(a)
        for sid, a in conn.execute(
            select(Stop.gtfs_stop_id, Stop.name_ko_aliases).where(Stop.source_id == "test-ko")
        )
    }
    assert aliases["101"] == []  # 아사쿠사는 표기가 갈리지 않는다
    assert aliases["102"] == ["구라마에"]  # 편집 이름이 표시, 표기법 이름은 별칭

    # 편집 목록에서 빠지면 기계 변환으로 돌아간다
    apply_korean_names(conn, "test-ko", KoreanNames(lines={}, stops={}))
    assert names()["102"] == ("구라마에", NameSource.machine)
    q = select(Stop.name_ko_aliases).where(Stop.source_id == "test-ko", Stop.gtfs_stop_id == "102")
    assert list(conn.execute(q).scalar_one()) == ["쿠라마에"]

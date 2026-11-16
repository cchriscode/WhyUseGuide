import os
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Connection, create_engine, func, select, update

from whyuseguide_api.db.models import Place, PlaceStatus, Stop
from whyuseguide_pipelines.gtfs import GtfsLine, GtfsStop, Network
from whyuseguide_pipelines.load import load_network
from whyuseguide_pipelines.places import (
    PlaceCandidate,
    load_places,
    parse_wikidata,
    popularity_from_sitelinks,
)


def binding(q: str, cls: str, links: int, **labels: str) -> dict[str, Any]:
    b: dict[str, Any] = {
        "item": {"value": f"http://www.wikidata.org/entity/{q}"},
        "cls": {"value": f"http://www.wikidata.org/entity/{cls}"},
        "links": {"value": str(links)},
        "loc": {"value": "Point(139.7967 35.7148)"},
    }
    for k, v in labels.items():
        b[k] = {"value": v}
    return b


def test_parse_merges_rows_and_picks_type() -> None:
    raw = {
        "results": {
            "bindings": [
                binding(
                    "Q1",
                    "Q5393308",
                    60,
                    ja="浅草寺",
                    ko="센소지",
                    en="Sensō-ji",
                    site="https://www.senso-ji.jp/",
                ),
                binding("Q1", "Q570116", 60, ja="浅草寺"),
                binding("Q2", "Q207694", 12, ja="東京国立博物館", en="Tokyo National Museum"),
                binding("Q3", "Q999", 4, ja="不明"),
            ]
        }
    }
    parsed = {c.ref: c for c in parse_wikidata(raw)}
    assert set(parsed) == {"Q1", "Q2"}  # 매핑되지 않는 분류는 뺀다
    q1 = parsed["Q1"]
    assert (q1.type, q1.name_local, q1.name_ko, q1.popularity) == ("신사·사찰", "浅草寺", "센소지", 5)
    assert q1.website == "https://www.senso-ji.jp/"
    assert (q1.lat, q1.lon) == (35.7148, 139.7967)
    q2 = parsed["Q2"]
    assert (q2.type, q2.name_ko, q2.popularity) == ("박물관", None, 3)


@pytest.mark.parametrize(("links", "level"), [(3, 1), (5, 2), (10, 3), (20, 4), (50, 5), (120, 5)])
def test_popularity(links: int, level: int) -> None:
    assert popularity_from_sitelinks(links) == level


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


def cand(ref: str, name: str, **kw: Any) -> PlaceCandidate:
    base: dict[str, Any] = {
        "name_ko": None,
        "name_en": None,
        "type": "박물관",
        "lat": 35.7148,
        "lon": 139.7967,
        "popularity": 3,
        "website": None,
    }
    return PlaceCandidate(ref=ref, name_local=name, **(base | kw))


@needs_db
def test_load_inserts_hidden_and_keeps_verified(conn: Connection) -> None:
    written, kept = load_places(
        conn, "test-wd", "tokyo", [cand("Q1", "浅草寺", type="신사·사찰"), cand("Q2", "博物館")]
    )
    assert (written, kept) == (2, 0)
    cols = select(
        Place.source_ref,
        Place.status,
        Place.verified_at,
        Place.default_stay_min,
        Place.indoor,
        Place.currency,
    ).where(Place.source == "test-wd")
    rows = {r.source_ref: r for r in conn.execute(cols)}
    assert rows["Q1"].status == PlaceStatus.hidden and rows["Q1"].verified_at is None
    assert rows["Q1"].default_stay_min == 45 and rows["Q2"].indoor and rows["Q2"].default_stay_min == 90
    assert rows["Q1"].currency == "JPY"

    # 편집자가 Q1을 검수했다 → 다시 받아도 이름을 덮어쓰지 않는다
    conn.execute(
        update(Place)
        .where(Place.source == "test-wd", Place.source_ref == "Q1")
        .values(verified_at=func.now(), name_ko="센소지(편집)")
    )
    written, kept = load_places(
        conn, "test-wd", "tokyo", [cand("Q1", "浅草寺", name_ko="센소지"), cand("Q2", "博物館2")]
    )
    assert (written, kept) == (1, 1)
    names = {
        r.source_ref: r
        for r in conn.execute(
            select(Place.source_ref, Place.name_ko, Place.name_local).where(Place.source == "test-wd")
        )
    }
    assert names["Q1"].name_ko == "센소지(편집)"
    assert names["Q2"].name_local == "博物館2"


@needs_db
def test_nearest_stop_is_filled(conn: Connection) -> None:
    # 빈 DB(CI)에서도 돌도록 역을 직접 넣는다. 장소 좌표 바로 옆이라 다른 역보다 가깝다.
    stop = GtfsStop("1", None, "浅草", None, 35.7147, 139.7967, None)
    line = GtfsLine("1", "浅草線", None, None, "test")
    load_network(conn, "test-stops", "tokyo", Network(lines=[line], stops=[stop], stop_lines={("1", "1")}))
    load_places(conn, "test-wd", "tokyo", [cand("Q1", "浅草寺")])
    stop_name = conn.execute(
        select(Stop.name_local).join(Place, Place.nearest_stop_id == Stop.id).where(Place.source == "test-wd")
    ).scalar_one()
    assert stop_name == "浅草"

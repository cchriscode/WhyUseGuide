"""API 통합 테스트 공통 준비. PostGIS가 필요하다(TEST_DATABASE_URL, CI는 서비스 컨테이너).

테스트 장소는 source = "test-api"로 넣고 끝나면 지운다. 테스트가 만든 사용자도 지운다(여행 등은 CASCADE).
"""

import asyncio
import os
import sys
from collections.abc import Iterator
from datetime import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, delete, func, insert, select

from whyuseguide_api.config import Settings
from whyuseguide_api.db.models import City, Place, PlaceHours, PlaceStatus, User
from whyuseguide_api.main import create_app

# psycopg 비동기 모드는 Windows 기본 이벤트 루프(Proactor)를 쓰지 못한다. 서버는 리눅스 컨테이너라 상관없다.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

DB_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(
    not DB_URL, reason="TEST_DATABASE_URL이 없으면 API 통합 테스트는 실행하지 않는다"
)

# 아사쿠사·우에노 일대에 모여 있는 테스트 장소(걸어서·대중교통으로 이어진다)
TEST_PLACES: list[dict[str, Any]] = [
    {
        "ref": "sensoji",
        "name_ko": "센소지",
        "name_local": "浅草寺",
        "type": "신사·사찰",
        "lat": 35.7148,
        "lon": 139.7967,
        "stay": 60,
        "rating": 4.5,
        "popularity": 5,
        "hours": None,
    },
    {
        "ref": "nakamise",
        "name_ko": "나카미세 거리",
        "name_local": "仲見世通り",
        "type": "골목 상점가",
        "lat": 35.7119,
        "lon": 139.7963,
        "stay": 45,
        "rating": 4.2,
        "popularity": 4,
        "hours": ("10:00", "19:00"),
    },
    {
        "ref": "tnm",
        "name_ko": "도쿄 국립박물관",
        "name_local": "東京国立博物館",
        "type": "박물관",
        "lat": 35.7188,
        "lon": 139.7765,
        "stay": 90,
        "rating": 4.6,
        "popularity": 5,
        "hours": ("09:30", "17:00"),
    },
    {
        "ref": "ameyoko",
        "name_ko": "아메요코 시장",
        "name_local": "アメ横",
        "type": "시장",
        "lat": 35.7100,
        "lon": 139.7745,
        "stay": 60,
        "rating": 4.1,
        "popularity": 4,
        "hours": ("10:00", "20:00"),
    },
    {
        "ref": "yanaka",
        "name_ko": "야나카 긴자",
        "name_local": "谷中銀座",
        "type": "골목 상점가",
        "lat": 35.7277,
        "lon": 139.7659,
        "stay": 60,
        "rating": 4.3,
        "popularity": 3,
        "hours": ("10:00", "18:00"),
    },
    {
        "ref": "kappabashi",
        "name_ko": "갓파바시 도구 거리",
        "name_local": "かっぱ橋道具街",
        "type": "쇼핑",
        "lat": 35.7139,
        "lon": 139.7883,
        "stay": 45,
        "rating": 4.0,
        "popularity": 3,
        "hours": ("10:00", "17:00"),
    },
    {
        "ref": "skytree",
        "name_ko": "도쿄 스카이트리",
        "name_local": "東京スカイツリー",
        "type": "전망",
        "lat": 35.7101,
        "lon": 139.8107,
        "stay": 90,
        "rating": 4.5,
        "popularity": 5,
        "hours": ("10:00", "21:00"),
    },
    {
        "ref": "monjya",
        "name_ko": "몬자야키 가게",
        "name_local": "もんじゃ焼き店",
        "type": "맛집",
        "lat": 35.7125,
        "lon": 139.7980,
        "stay": 60,
        "rating": 4.2,
        "popularity": 2,
        "hours": ("11:00", "22:00"),
    },
    {
        "ref": "uenopark",
        "name_ko": "우에노 공원",
        "name_local": "上野恩賜公園",
        "type": "공원",
        "lat": 35.7148,
        "lon": 139.7731,
        "stay": 60,
        "rating": 4.4,
        "popularity": 4,
        "hours": None,
    },
    {
        "ref": "hidden",
        "name_ko": "숨김 장소",
        "name_local": "非公開",
        "type": "공원",
        "lat": 35.715,
        "lon": 139.775,
        "stay": 30,
        "rating": 3.0,
        "popularity": 1,
        "hours": None,
        "status": PlaceStatus.hidden,
    },
]


def _t(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


@pytest.fixture(scope="session")
def db() -> Iterator[Engine]:
    assert DB_URL
    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        conn.execute(delete(Place).where(Place.source == "test-api"))
        city_id = conn.execute(select(City.id).where(City.code == "tokyo")).scalar_one()
        for p in TEST_PLACES:
            pid = conn.execute(
                insert(Place)
                .values(
                    city_id=city_id,
                    name_ko=p["name_ko"],
                    name_local=p["name_local"],
                    type=p["type"],
                    popularity=p["popularity"],
                    rating=p["rating"],
                    default_stay_min=p["stay"],
                    currency="JPY",
                    location=func.ST_GeogFromText(f"SRID=4326;POINT({p['lon']} {p['lat']})"),
                    status=p.get("status", PlaceStatus.active),
                    source="test-api",
                    source_ref=p["ref"],
                )
                .returning(Place.id)
            ).scalar_one()
            if p["hours"]:
                for wd in range(7):
                    conn.execute(
                        insert(PlaceHours).values(
                            place_id=pid,
                            weekday=wd,
                            open_time=_t(p["hours"][0]),
                            close_time=_t(p["hours"][1]),
                        )
                    )
    yield engine
    with engine.begin() as conn:
        conn.execute(delete(Place).where(Place.source == "test-api"))
    engine.dispose()


@pytest.fixture
def client(db: Engine) -> Iterator[TestClient]:
    assert DB_URL
    # OTP는 끈다: 로컬에 OTP가 떠 있는지에 따라 결과가 달라지지 않게(시간표 구간은 test_timed.py)
    with TestClient(create_app(Settings(database_url=DB_URL, otp_timed_legs=False))) as c:
        yield c


@pytest.fixture
def token(client: TestClient, db: Engine) -> Iterator[str]:
    """게스트 토큰. 테스트가 끝나면 그 사용자(와 여행 등)를 지운다."""
    body = client.post("/v1/auth/guest").json()
    yield body["token"]
    with db.begin() as conn:
        conn.execute(delete(User).where(User.id == body["userId"]))


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def place_id(db: Engine, ref: str) -> str:
    with db.connect() as conn:
        return str(
            conn.execute(
                select(Place.id).where(Place.source == "test-api", Place.source_ref == ref)
            ).scalar_one()
        )

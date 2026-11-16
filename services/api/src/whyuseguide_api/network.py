"""도시 노선망 조회: 환승할 수 있는 노선 쌍, 장소별 가까운 역 (근사 경로 §5.3의 입력).

SQL과 기준 거리는 여기 한 곳에만 둔다. API(bridge.py, 비동기 세션)와 벤치마크(data/pipelines, 동기 연결)가
같은 SQL을 실행하고 같은 함수로 결과를 정리한다. 기준 거리는 엔진 상수(PRD §5.0)에서 읽는다.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import TextClause, text

from itinerary_engine.config import constants

# 서로 TRANSFER_KM_MAX 안에 역이 있는 두 노선은 환승할 수 있다고 본다. 매개변수: city(도시 id), m(미터)
LINE_LINKS_SQL: TextClause = text(
    """
    SELECT DISTINCT LEAST(x1.line_id::text, x2.line_id::text) AS a,
                    GREATEST(x1.line_id::text, x2.line_id::text) AS b
    FROM stops s1 JOIN stops s2 ON s1.id <> s2.id AND ST_DWithin(s1.location, s2.location, :m)
    JOIN stop_lines x1 ON x1.stop_id = s1.id JOIN stop_lines x2 ON x2.stop_id = s2.id
    WHERE s1.city_id = :city AND x1.line_id <> x2.line_id
    """
)

# 장소에서 STATION_KM_MAX 안의 역: 노선마다 가장 가까운 역까지 거리. 매개변수: ids(장소 id 목록), m(미터)
PLACE_STATIONS_SQL: TextClause = text(
    """
    SELECT p.id AS pid, sl.line_id::text AS line, min(ST_Distance(s.location, p.location)) AS m
    FROM places p JOIN stops s ON ST_DWithin(s.location, p.location, :m)
    JOIN stop_lines sl ON sl.stop_id = s.id
    WHERE p.id = ANY(:ids) GROUP BY p.id, sl.line_id
    """
)


def line_links_params(city_id: uuid.UUID) -> dict[str, Any]:
    return {"city": city_id, "m": constants()["approx"]["transferKmMax"] * 1000}


def place_stations_params(place_ids: Sequence[uuid.UUID]) -> dict[str, Any]:
    return {"ids": list(place_ids), "m": constants()["approx"]["stationKmMax"] * 1000}


def line_links(rows: Iterable[Any]) -> list[tuple[str, str]]:
    return [(r.a, r.b) for r in rows]


def stations_by_place(rows: Iterable[Any]) -> dict[uuid.UUID, dict[str, float]]:
    """{장소 id: {노선 id: 가장 가까운 역까지 km(소수 셋째 자리)}}. 역이 없는 장소는 빠진다."""
    out: dict[uuid.UUID, dict[str, float]] = defaultdict(dict)
    for r in rows:
        out[r.pid][r.line] = round(r.m / 1000, 3)
    return dict(out)

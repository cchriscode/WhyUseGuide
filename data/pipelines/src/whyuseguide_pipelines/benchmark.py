"""경로 정확도 벤치마크 (PRD §8).

같은 장소 쌍·출발 시각으로 두 경로 계산 결과를 비교한다. 지금 돌릴 수 있는 비교:
- otp-vs-approx: OTP(시간표·근사 노선) vs 엔진 근사식(§5.3, 앱 오프라인 계산). 근사 계수 보정 근거.
  기준 경로가 정밀 노선(실제 시간표)만 탔는지, 근사 노선을 탔는지, 역이 먼 장소인지로 나눠서도 집계한다.
- free-data-impact: OTP 전체 vs 근사 노선(JR·사철·공항선)을 뺀 OTP. 근사 노선이 경로를 얼마나 바꾸는지.
상용 API(駅すぱあと 등)는 같은 Planner 형태로 붙인다(API 키가 생긴 뒤).
"""

import json
import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import Connection, text

from itinerary_engine.config import constants
from itinerary_engine.models import City
from itinerary_engine.route import Endpoint, approx_leg, straight_km


@dataclass(frozen=True)
class Spot:
    id: str
    name: str
    lat: float
    lon: float
    stations: dict[str, float] = field(default_factory=dict, hash=False)  # 노선 id → 가장 가까운 역 km


@dataclass(frozen=True)
class Summary:
    minutes: int
    transfers: int
    first_route: str | None  # 첫 승차 노선 id(걷기만 하면 None)
    estimated: bool = False  # 근사 노선(시간표가 아닌 배차 간격 근사)을 탔다


Planner = Callable[[Spot, Spot, str, str], Summary | None]  # (출발, 도착, 날짜, 시각)

# PRD §8 출발 시각: 평일 08:30·13:00·18:00, 주말 11:00
DEPARTURES = (
    ("2026-11-17", "08:30"),
    ("2026-11-17", "13:00"),
    ("2026-11-17", "18:00"),
    ("2026-11-21", "11:00"),
)


def sample_pairs(
    spots: Sequence[Spot], count: int, seed: int, min_km: float = 1.5, max_km: float = 15.0
) -> list[tuple[Spot, Spot]]:
    """고정 시드로 장소 쌍을 뽑는다(매번 같은 표본). 너무 가깝거나 먼 쌍은 뺀다."""
    rng = random.Random(seed)
    ordered = sorted(spots, key=lambda s: s.id)
    pairs: list[tuple[Spot, Spot]] = []
    seen: set[tuple[str, str]] = set()
    attempts = 0
    while len(pairs) < count and attempts < count * 200:
        attempts += 1
        a, b = rng.sample(ordered, 2)
        km = straight_km(Endpoint(a.id, a.lat, a.lon), Endpoint(b.id, b.lat, b.lon))
        if min_km <= km <= max_km and (a.id, b.id) not in seen:
            seen.add((a.id, b.id))
            pairs.append((a, b))
    return pairs


@dataclass(frozen=True)
class Metrics:
    pairs: int
    failed_ours: int
    failed_ref: int
    compared: int
    median_abs_diff_min: float | None
    within_5_min_pct: float | None
    within_10_min_pct: float | None
    mean_diff_min: float | None  # 양수면 우리가 더 오래 걸린다고 본다
    transfers_match_pct: float | None
    first_route_match_pct: float | None


def metrics(results: Sequence[tuple[Summary | None, Summary | None]]) -> Metrics:
    """results: (우리, 기준) 쌍들."""
    both = [(o, r) for o, r in results if o is not None and r is not None]
    diffs = [o.minutes - r.minutes for o, r in both]
    routed = [(o, r) for o, r in both if o.first_route is not None and r.first_route is not None]

    def pct(n: int, d: int) -> float | None:
        return round(100 * n / d, 1) if d else None

    return Metrics(
        pairs=len(results),
        failed_ours=sum(1 for o, _ in results if o is None),
        failed_ref=sum(1 for _, r in results if r is None),
        compared=len(both),
        median_abs_diff_min=statistics.median(abs(d) for d in diffs) if diffs else None,
        within_5_min_pct=pct(sum(1 for d in diffs if abs(d) <= 5), len(diffs)),
        within_10_min_pct=pct(sum(1 for d in diffs if abs(d) <= 10), len(diffs)),
        mean_diff_min=round(statistics.fmean(diffs), 1) if diffs else None,
        transfers_match_pct=pct(sum(1 for o, r in both if o.transfers == r.transfers), len(both)),
        first_route_match_pct=pct(sum(1 for o, r in routed if o.first_route == r.first_route), len(routed)),
    )


# --- 경로 계산기


class OtpPlanner:
    """OTP GTFS GraphQL plan. banned_agencies: 이 운영사 노선은 쓰지 않는다(쉼표 구분 gtfsId).

    estimated_agencies: 근사 노선 운영사 이름. 이 노선을 탄 경로는 Summary.estimated = True.
    """

    QUERY = """query($fl:Float!,$fo:Float!,$tl:Float!,$to:Float!,$date:String!,$time:String!,$banned:String) {
      plan(from:{lat:$fl,lon:$fo}, to:{lat:$tl,lon:$to}, date:$date, time:$time, numItineraries:3,
           transportModes:[{mode:WALK},{mode:TRANSIT}], banned:{agencies:$banned}) {
        itineraries { duration numberOfTransfers legs { mode route { gtfsId agency { name } } } }
      }
    }"""

    def __init__(
        self,
        client: httpx.Client,
        url: str,
        banned_agencies: str | None = None,
        estimated_agencies: frozenset[str] = frozenset(),
    ) -> None:
        self.client, self.url, self.banned = client, url, banned_agencies
        self.estimated_agencies = estimated_agencies

    def __call__(self, a: Spot, b: Spot, date: str, time: str) -> Summary | None:
        variables = {
            "fl": a.lat,
            "fo": a.lon,
            "tl": b.lat,
            "to": b.lon,
            "date": date,
            "time": time,
            "banned": self.banned,
        }
        resp = self.client.post(f"{self.url}/otp/gtfs/v1", json={"query": self.QUERY, "variables": variables})
        resp.raise_for_status()
        itineraries = resp.json()["data"]["plan"]["itineraries"]
        if not itineraries:
            return None
        best = min(itineraries, key=lambda it: it["duration"])
        routes = [leg["route"] for leg in best["legs"] if leg["route"]]
        return Summary(
            minutes=round(best["duration"] / 60),
            transfers=best["numberOfTransfers"],
            first_route=routes[0]["gtfsId"] if routes else None,
            estimated=any(r["agency"]["name"] in self.estimated_agencies for r in routes),
        )


def approx_planner(city: City) -> Planner:
    """엔진 근사식(§5.3, 균형 모드). 노선 id는 비교하지 않는다(None)."""

    def plan(a: Spot, b: Spot, date: str, time: str) -> Summary | None:
        leg = approx_leg(
            Endpoint(a.id, a.lat, a.lon, a.stations),
            Endpoint(b.id, b.lat, b.lon, b.stations),
            "balance",
            "transit",
            city,
        )
        return Summary(minutes=leg.minutes, transfers=leg.transfers, first_route=None)

    return plan


# --- DB에서 표본·노선 정보


def load_spots(conn: Connection, city_code: str) -> list[Spot]:
    """장소와 노선별 가장 가까운 역까지 km(근사식이 직통·환승과 역까지 걷기를 계산하는 데 쓴다).

    API(bridge.place_stations)와 같은 기준: 역 STATION_KM_MAX 안, km는 소수 셋째 자리.
    """
    rows = conn.execute(
        text(
            """
            SELECT p.id::text AS id, coalesce(p.name_ko, p.name_local) AS name,
                   ST_Y(p.location::geometry) AS lat, ST_X(p.location::geometry) AS lon,
                   sl.line_id::text AS line, min(ST_Distance(s.location, p.location)) AS m
            FROM places p JOIN cities c ON c.id = p.city_id
            LEFT JOIN stops s ON ST_DWithin(s.location, p.location, :m)
            LEFT JOIN stop_lines sl ON sl.stop_id = s.id
            WHERE c.code = :city
            GROUP BY p.id, sl.line_id
            """
        ),
        {"city": city_code, "m": constants()["approx"]["stationKmMax"] * 1000},
    )
    spots: dict[str, Spot] = {}
    for r in rows:
        spot = spots.setdefault(r.id, Spot(r.id, r.name, r.lat, r.lon))
        if r.line is not None:
            spot.stations[r.line] = round(r.m / 1000, 3)
    return list(spots.values())


def load_city(conn: Connection, city_code: str) -> City:
    """환승 가능 노선 쌍 = 서로 300m 안에 역이 있는 노선."""
    rows = conn.execute(
        text(
            """
            SELECT DISTINCT LEAST(l1.id::text, l2.id::text) AS a, GREATEST(l1.id::text, l2.id::text) AS b
            FROM stops s1 JOIN stops s2 ON s1.id <> s2.id AND ST_DWithin(s1.location, s2.location, 300)
            JOIN stop_lines x1 ON x1.stop_id = s1.id JOIN lines l1 ON l1.id = x1.line_id
            JOIN stop_lines x2 ON x2.stop_id = s2.id JOIN lines l2 ON l2.id = x2.line_id
            JOIN cities c ON c.id = l1.city_id
            WHERE c.code = :city AND l1.id <> l2.id
            """
        ),
        {"city": city_code},
    )
    fare = conn.execute(
        text("SELECT fare_table FROM cities WHERE code = :city"), {"city": city_code}
    ).scalar_one()
    return City.model_validate({"lineLinks": [(r.a, r.b) for r in rows], **(fare or {})})


SEGMENTS = (
    "역 가까운 장소·정밀 노선만(실제 시간표)",
    "역 가까운 장소·근사 노선 포함",
    "역이 먼 장소 포함",
)


def segment(a: Spot, b: Spot, ref: Summary | None) -> str:
    """역 가까운 장소 = 양쪽 다 STATION_KM_MAX 안에 역이 있다. 기준 경로가 없으면 정밀 쪽에 넣는다."""
    if not (a.stations and b.stations):
        return SEGMENTS[2]
    return SEGMENTS[1] if ref is not None and ref.estimated else SEGMENTS[0]


def run(
    pairs: Sequence[tuple[Spot, Spot]], ours: Planner, ref: Planner
) -> tuple[Metrics, list[dict[str, Any]], dict[str, Metrics]]:
    results: list[tuple[Summary | None, Summary | None]] = []
    groups: dict[str, list[tuple[Summary | None, Summary | None]]] = {k: [] for k in SEGMENTS}
    detail: list[dict[str, Any]] = []
    for a, b in pairs:
        for date, time in DEPARTURES:
            o, r = ours(a, b, date, time), ref(a, b, date, time)
            results.append((o, r))
            groups[segment(a, b, r)].append((o, r))
            detail.append(
                {
                    "from": a.name,
                    "to": b.name,
                    "date": date,
                    "time": time,
                    "segment": segment(a, b, r),
                    "ours": asdict(o) if o else None,
                    "ref": asdict(r) if r else None,
                }
            )
    return metrics(results), detail, {k: metrics(v) for k, v in groups.items()}


def report(
    name: str,
    m: Metrics,
    detail: list[dict[str, Any]],
    segments: dict[str, Metrics] | None = None,
    worst: int = 20,
) -> dict[str, Any]:
    """차이가 큰 상위 worst건은 사람이 원인을 분류한다(PRD §8)."""
    comparable = [d for d in detail if d["ours"] and d["ref"]]
    comparable.sort(key=lambda d: -abs(d["ours"]["minutes"] - d["ref"]["minutes"]))
    return {
        "name": name,
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "metrics": asdict(m),
        "segments": {k: asdict(v) for k, v in (segments or {}).items()},
        "worst": comparable[:worst],
    }


def save(path: Any, reports: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")

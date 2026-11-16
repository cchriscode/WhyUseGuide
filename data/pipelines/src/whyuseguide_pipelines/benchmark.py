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
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import Connection, text

from itinerary_engine.models import City
from itinerary_engine.route import Endpoint, approx_leg, straight_km
from whyuseguide_api import network, otp

NUM_ITINERARIES = 3  # 기준 경로로 받는 후보 수. 이 중 가장 짧은 것을 쓴다(회차끼리 비교하려면 바꾸지 않는다).


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
WEEKDAY, WEEKDAY_TIMES = 1, ("08:30", "13:00", "18:00")  # 화요일
WEEKEND, WEEKEND_TIMES = 5, ("11:00",)  # 토요일


def departures(base: date) -> tuple[tuple[str, str], ...]:
    """기준일 이후 첫 화요일·토요일의 출발 (날짜, 시각).

    날짜를 코드에 박아 두면 새로 받은 데이터의 운행 기간 밖이 되어도 오류 없이 그 노선만 빠진다
    (2026-11-16 도에이 버스).
    """

    def first(weekday: int) -> str:
        return (base + timedelta(days=(weekday - base.weekday()) % 7)).isoformat()

    weekday, weekend = first(WEEKDAY), first(WEEKEND)
    return tuple((weekday, t) for t in WEEKDAY_TIMES) + tuple((weekend, t) for t in WEEKEND_TIMES)


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
    """OTP 경로(질의문·응답 해석은 whyuseguide_api.otp). 요청이 실패하면 OtpError로 벤치마크를 멈춘다.

    banned_agencies: 이 운영사 노선은 쓰지 않는다(쉼표 구분 gtfsId).
    estimated_agencies: 근사 노선 운영사 이름. 이 노선을 탄 경로는 Summary.estimated = True.
    """

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
        request = otp.plan_request(
            (a.lat, a.lon), (b.lat, b.lon), date, time, num=NUM_ITINERARIES, banned_agencies=self.banned
        )
        plan = otp.fetch_plan_sync(self.client, self.url, request)
        if not plan.itineraries:
            return None
        best = min(plan.itineraries, key=lambda it: it["duration"])
        routes = otp.routes(best)
        return Summary(
            minutes=round(best["duration"] / 60),
            transfers=best["numberOfTransfers"],
            first_route=routes[0]["gtfsId"] if routes else None,
            estimated=bool(otp.agency_names(best) & self.estimated_agencies),
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

    역 거리는 API와 같은 SQL·같은 기준(whyuseguide_api.network)이다.
    """
    places = conn.execute(
        text(
            """
            SELECT p.id, coalesce(p.name_ko, p.name_local) AS name,
                   ST_Y(p.location::geometry) AS lat, ST_X(p.location::geometry) AS lon
            FROM places p JOIN cities c ON c.id = p.city_id WHERE c.code = :city
            """
        ),
        {"city": city_code},
    ).all()
    stations = network.stations_by_place(
        conn.execute(network.PLACE_STATIONS_SQL, network.place_stations_params([p.id for p in places]))
    )
    return [Spot(str(p.id), p.name, p.lat, p.lon, stations.get(p.id, {})) for p in places]


def load_city(conn: Connection, city_code: str) -> City:
    """환승 가능 노선 쌍(API와 같은 SQL·같은 기준, whyuseguide_api.network)과 운임표."""
    city_id, fare = conn.execute(
        text("SELECT id, fare_table FROM cities WHERE code = :city"), {"city": city_code}
    ).one()
    rows = conn.execute(network.LINE_LINKS_SQL, network.line_links_params(city_id))
    return City.model_validate({"lineLinks": network.line_links(rows), **(fare or {})})


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
    pairs: Sequence[tuple[Spot, Spot]], ours: Planner, ref: Planner, when: Sequence[tuple[str, str]]
) -> tuple[Metrics, list[dict[str, Any]], dict[str, Metrics]]:
    results: list[tuple[Summary | None, Summary | None]] = []
    groups: dict[str, list[tuple[Summary | None, Summary | None]]] = {k: [] for k in SEGMENTS}
    detail: list[dict[str, Any]] = []
    for a, b in pairs:
        for day, time in when:
            o, r = ours(a, b, day, time), ref(a, b, day, time)
            results.append((o, r))
            groups[segment(a, b, r)].append((o, r))
            detail.append(
                {
                    "from": a.name,
                    "to": b.name,
                    "date": day,
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

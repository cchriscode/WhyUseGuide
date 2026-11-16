"""구간 경로 선택 (PRD §5.3). 알려진 구간(OTP·캐시)이 있으면 그것을, 없으면 근사 규칙을 쓴다."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from itinerary_engine.config import ceil_min, constants, round2, to_min
from itinerary_engine.models import City, Leg, LegMode, PlanMode, TaxiFare, Transport


@dataclass(frozen=True)
class Endpoint:
    id: str
    lat: float
    lon: float
    # 노선 id → 그 노선의 가장 가까운 역까지 직선 km (STATION_KM_MAX 안).
    # 빈 값 = 가까운 역이 없음, None = 모름(숙소 등 역 정보가 없는 지점).
    stations: Mapping[str, float] | None = field(default=None, hash=False)


@dataclass(frozen=True)
class RouteLeg:
    mode: LegMode
    minutes: int
    fare: int
    km: float
    estimated: bool
    transfers: int = 0
    label: str = ""
    wait: int = 0  # 출발지에서 더 머무는 분(시간표 구간만)


def straight_km(a: Endpoint, b: Endpoint) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def route_km(a: Endpoint, b: Endpoint) -> float:
    """경로 거리 = 직선 거리 × 1.3, 소수 둘째 자리 반올림(TS 포트와 같은 값을 쓰기 위해)."""
    return round2(straight_km(a, b) * constants()["approx"]["routeFactor"])


def _nearest_km(stations: Mapping[str, float] | None) -> float:
    approx = constants()["approx"]
    if stations is None:
        return float(approx["stationKmUnknown"])
    return min(stations.values(), default=float(approx["stationKmMax"]))


def line_choice(a: Endpoint, b: Endpoint, city: City) -> tuple[int, float]:
    """(환승 횟수, 양끝에서 탈 역까지 직선 km 합).

    같은 노선이 있으면 직통(0), 두 노선이 만나는 환승역이 있으면 1, 없으면 2.
    직통·환승 1회는 양끝 역까지 거리 합이 가장 작은 노선(쌍)을 고르고, 2회는 각자 가장 가까운 역을 쓴다.
    """
    sa, sb = a.stations or {}, b.stations or {}
    shared = set(sa) & set(sb)
    if shared:
        return 0, min(sa[x] + sb[x] for x in shared)
    links = {frozenset(p) for p in city.line_links}
    linked = [sa[x] + sb[y] for x in sa for y in sb if frozenset((x, y)) in links]
    if linked:
        return 1, min(linked)
    return 2, _nearest_km(a.stations) + _nearest_km(b.stations)


def transfers_between(a: Endpoint, b: Endpoint, city: City) -> int:
    return line_choice(a, b, city)[0]


def transit_fare(km: float, city: City) -> int:
    for band in city.fare_bands:
        if km <= band.max_km:
            return band.fare
    return city.fare_bands[-1].fare if city.fare_bands else 0


def taxi_fare(km: float, t: TaxiFare) -> int:
    return t.base + max(0, math.ceil((km - t.base_km) * t.per_km - 1e-9))


def _walk(km: float) -> RouteLeg:
    return RouteLeg("walk", ceil_min(km * 1000 / constants()["approx"]["walkMetersPerMin"]), 0, km, True)


def _transit(km: float, transfers: int, access_km: float, city: City) -> RouteLeg:
    """역까지 걷기(직선 km × 1.3) + 기본 + km당 시간."""
    approx = constants()["approx"]
    base, per_km = approx[("direct", "transfer1", "transfer2")[transfers]]
    access_walk = round2(access_km * approx["routeFactor"]) * 1000 / approx["walkMetersPerMin"]
    minutes = ceil_min(access_walk + base + per_km * km)
    return RouteLeg("transit", minutes, transit_fare(km, city), km, True, transfers)


def taxi_leg(km: float, fare: TaxiFare) -> RouteLeg:
    base, per_km = constants()["approx"]["taxi"]
    return RouteLeg("taxi", ceil_min(base + per_km * km), taxi_fare(km, fare), km, True)


def _car(km: float, city: City, parking_fee: int) -> RouteLeg:
    car = constants()["approx"]["car"]
    minutes = (
        car["base"]
        + car["cityPerKm"] * min(km, car["cityKm"])
        + car["highwayPerKm"] * max(km - car["cityKm"], 0)
    )
    fare = math.floor(km * city.fuel_per_km + 0.5) + parking_fee
    return RouteLeg("car", ceil_min(minutes) + constants()["parkMin"], fare, km, True)


def approx_leg(
    a: Endpoint,
    b: Endpoint,
    plan_mode: PlanMode,
    transport: Transport,
    city: City,
    parking_fee: int = 0,
) -> RouteLeg:
    c = constants()
    km = route_km(a, b)
    if transport == "car":
        return _car(km, city, parking_fee)
    walk = _walk(km)
    transit = _transit(km, *line_choice(a, b, city), city)
    if plan_mode == "balance":
        return walk if km <= c["walkOnlyKm"] else transit
    if plan_mode == "cheap":
        return walk if km <= c["walkCheapKm"] else transit
    # 빠르게: 대중교통 최단. 걷는 게 더 빠르면 걷고, 환승 구간이 길면 택시와 비교한다
    # (택시 운임이 있는 도시만).
    best = walk if walk.minutes <= transit.minutes else transit
    if best is transit and city.taxi and transit.transfers >= 1 and km > c["taxiCompareKm"]:
        taxi = taxi_leg(km, city.taxi)
        if taxi.minutes < transit.minutes:
            best = taxi
    return best


def choose_leg(
    a: Endpoint,
    b: Endpoint,
    plan_mode: PlanMode,
    transport: Transport,
    city: City,
    known: dict[tuple[str, str, PlanMode], Leg],
    parking_fee: int = 0,
    ready: int | None = None,
) -> RouteLeg:
    """ready = 출발 준비 시각(분). 시간표 구간(depart_at 있음)은 ready가 같을 때만 쓴다."""
    leg = known.get((a.id, b.id, plan_mode))
    if leg is not None and (leg.depart_at is None or (ready is not None and to_min(leg.depart_at) == ready)):
        return RouteLeg(
            leg.mode, leg.minutes, leg.fare, leg.km, leg.estimated, leg.transfers, leg.label, leg.wait_min
        )
    return approx_leg(a, b, plan_mode, transport, city, parking_fee)

"""구간 경로 선택 (PRD §5.3). 알려진 구간(OTP·캐시)이 있으면 그것을, 없으면 근사 규칙을 쓴다."""

import math
from dataclasses import dataclass

from itinerary_engine.config import ceil_min, constants, round2
from itinerary_engine.models import City, Leg, LegMode, PlanMode, Transport


@dataclass(frozen=True)
class Endpoint:
    id: str
    lat: float
    lon: float
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteLeg:
    mode: LegMode
    minutes: int
    fare: int
    km: float
    estimated: bool
    transfers: int = 0
    label: str = ""


def straight_km(a: Endpoint, b: Endpoint) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def route_km(a: Endpoint, b: Endpoint) -> float:
    """경로 거리 = 직선 거리 × 1.3, 소수 둘째 자리 반올림(TS 포트와 같은 값을 쓰기 위해)."""
    return round2(straight_km(a, b) * constants()["approx"]["routeFactor"])


def transfers_between(a: Endpoint, b: Endpoint, city: City) -> int:
    """같은 노선 → 0, 두 노선이 만나는 환승역이 있으면 1, 없으면 2."""
    if set(a.lines) & set(b.lines):
        return 0
    links = {frozenset(p) for p in city.line_links}
    if any(frozenset((x, y)) in links for x in a.lines for y in b.lines):
        return 1
    return 2


def transit_fare(km: float, city: City) -> int:
    for band in city.fare_bands:
        if km <= band.max_km:
            return band.fare
    return city.fare_bands[-1].fare if city.fare_bands else 0


def taxi_fare(km: float, city: City) -> int:
    t = city.taxi
    return t.base + max(0, math.ceil((km - t.base_km) * t.per_km - 1e-9))


def _walk(km: float) -> RouteLeg:
    return RouteLeg("walk", ceil_min(km * 1000 / constants()["approx"]["walkMetersPerMin"]), 0, km, True)


def _transit(km: float, transfers: int, city: City) -> RouteLeg:
    approx = constants()["approx"]
    base, per_km = approx[("direct", "transfer1", "transfer2")[transfers]]
    return RouteLeg("transit", ceil_min(base + per_km * km), transit_fare(km, city), km, True, transfers)


def _taxi(km: float, city: City) -> RouteLeg:
    base, per_km = constants()["approx"]["taxi"]
    return RouteLeg("taxi", ceil_min(base + per_km * km), taxi_fare(km, city), km, True)


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
    transit = _transit(km, transfers_between(a, b, city), city)
    if plan_mode == "balance":
        return walk if km <= c["walkOnlyKm"] else transit
    if plan_mode == "cheap":
        return walk if km <= c["walkCheapKm"] else transit
    # 빠르게: 대중교통 최단. 걷는 게 더 빠르면 걷고, 환승 구간이 길면 택시와 비교한다.
    best = walk if walk.minutes <= transit.minutes else transit
    if best is transit and transit.transfers >= 1 and km > c["taxiCompareKm"]:
        taxi = _taxi(km, city)
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
) -> RouteLeg:
    leg = known.get((a.id, b.id, plan_mode))
    if leg is not None:
        return RouteLeg(leg.mode, leg.minutes, leg.fare, leg.km, leg.estimated, leg.transfers, leg.label)
    return approx_leg(a, b, plan_mode, transport, city, parking_fee)

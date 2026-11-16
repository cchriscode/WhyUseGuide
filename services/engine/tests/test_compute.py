import json

import pytest
from builders import day, leg, place, stops

from itinerary_engine.compute import compute
from itinerary_engine.config import constants
from itinerary_engine.fixtures import SHARED_CONSTANTS
from itinerary_engine.models import City, FareBand, LegRow, TaxiFare
from itinerary_engine.route import Endpoint, approx_leg, line_choice, route_km


def test_constants_match_shared_file() -> None:
    shared = json.loads(SHARED_CONSTANTS.read_text(encoding="utf-8"))
    assert constants() == shared


def test_place_types_have_one_definition() -> None:
    """식사 유형과 픽스처가 쓰는 유형은 모두 constants.json의 placeTypes 안에 있다."""
    types = set(constants()["placeTypes"])
    assert len(types) == len(constants()["placeTypes"])
    for meal in ("lunch", "dinner"):
        assert set(constants()["mealTypes"][meal]) <= types


def test_basic_timeline_rounds_arrival_to_5_minutes() -> None:
    d = day(
        [{"placeId": "A"}, {"placeId": "B", "stayMin": 90}],
        [place("A", open="09:00", costPerPerson=500), place("B", open="10:00", costPerPerson=1000)],
        [leg("A", "B", 13)],
    )
    r = compute(d)
    a, b = stops(r)
    assert (a.arrive, a.start, a.end) == ("09:00", "09:00", "10:00")
    assert (b.arrive, b.wait, b.start, b.end) == ("10:15", 0, "10:15", "11:45")  # 10:13 → 10:15
    assert r.totals.move_min == 13
    assert r.totals.fare_per_person == 180
    assert r.totals.spend_per_person == 180 + 1500
    assert (r.totals.end, r.totals.issues, r.totals.over_end) == ("11:45", 0, False)


def test_fixed_booking_waits_or_is_late() -> None:
    places = [place("A"), place("B")]
    early = compute(
        day([{"placeId": "A"}, {"placeId": "B", "fixedAt": "11:00"}], places, [leg("A", "B", 10)])
    )
    b = stops(early)[1]
    assert (b.arrive, b.wait, b.start, b.issue) == ("10:10", 50, "11:00", None)

    within_tol = compute(
        day([{"placeId": "A"}, {"placeId": "B", "fixedAt": "10:00"}], places, [leg("A", "B", 10)])
    )
    assert stops(within_tol)[1].issue is None  # 10:10 도착, 허용 10분

    late = compute(day([{"placeId": "A"}, {"placeId": "B", "fixedAt": "10:00"}], places, [leg("A", "B", 13)]))
    assert stops(late)[1].issue == "late"  # 10:15 도착
    assert late.totals.issues == 1


def test_open_wait_too_long_and_closing() -> None:
    places = [place("A", stayMin=60), place("B", open="12:00"), place("C", close="12:30", defaultStayMin=60)]
    r = compute(day([{"placeId": "A"}, {"placeId": "B"}], places, [leg("A", "B", 5)]))
    assert stops(r)[1].issue == "open"  # 10:05 도착, 12:00 오픈 → 115분 대기
    assert stops(r)[1].wait == 115

    r = compute(day([{"placeId": "A", "stayMin": 150}, {"placeId": "C"}], places, [leg("A", "C", 5)]))
    assert stops(r)[1].issue == "close"  # 11:35 시작 + 60분 > 12:30


def test_closed_day_and_last_entry() -> None:
    places = [place("A"), place("B", closed=True), place("C", lastEntry="09:30")]
    r = compute(
        day(
            [{"placeId": "A"}, {"placeId": "B"}, {"placeId": "C"}],
            places,
            [leg("A", "B", 5), leg("B", "C", 5)],
        )
    )
    assert [s.issue for s in stops(r)] == [None, "close", "close"]


def test_delay_shifts_following_stops_or_is_absorbed() -> None:
    places = [place("A"), place("B"), place("C")]
    legs = [leg("A", "B", 10), leg("B", "C", 10)]
    base = stops(compute(day([{"placeId": "A"}, {"placeId": "B"}, {"placeId": "C"}], places, legs)))
    late = compute(
        day(
            [{"placeId": "A"}, {"placeId": "B"}, {"placeId": "C"}],
            places,
            legs,
            delay={"gap": 1, "minutes": 20},
        )
    )
    assert stops(late)[1].arrive == "10:30" and base[1].arrive == "10:10"
    leg_rows = [r for r in late.rows if isinstance(r, LegRow)]
    assert leg_rows[0].delay_min == 20

    fixed = compute(
        day(
            [{"placeId": "A"}, {"placeId": "B", "fixedAt": "11:00"}],
            places,
            legs,
            delay={"gap": 1, "minutes": 20},
        )
    )
    assert stops(fixed)[1].start == "11:00"  # 20분 지연이 예약 전 대기에 흡수된다


def test_timed_leg_departs_with_the_train() -> None:
    """시간표 구간: 출발 준비 시각이 같으면 열차에 맞춰 더 머문 뒤 출발, 다르면 근사한다."""
    timed = {**leg("A", "B", 18), "departAt": "10:00", "waitMin": 7}
    late_timed = {**leg("B", "C", 18), "departAt": "11:00", "waitMin": 3}  # 실제 준비 시각은 11:25
    result = compute(
        day(
            [{"placeId": "A"}, {"placeId": "B"}, {"placeId": "C"}],
            [place("A"), place("B"), place("C")],
            [timed, late_timed],
        )
    )
    legs = [r for r in result.rows if isinstance(r, LegRow)]
    assert (legs[0].depart, legs[0].wait_min, legs[0].minutes) == ("10:07", 7, 18)
    assert stops(result)[1].arrive == "10:25"
    assert (legs[1].depart, legs[1].wait_min, legs[1].minutes) == ("11:25", 0, 0)  # 같은 좌표라 근사 도보 0분
    assert result.totals.move_min == 18  # 더 머무는 시간은 이동 시간이 아니다

    delayed = compute(
        day(
            [{"placeId": "A"}, {"placeId": "B"}],
            [place("A"), place("B")],
            [{**timed, "departAt": "10:20"}],
            delay={"gap": 1, "minutes": 20},
        )
    )
    first = [r for r in delayed.rows if isinstance(r, LegRow)][0]
    assert (first.depart, first.wait_min) == ("10:27", 7)  # 지연 20분 뒤 준비 → 7분 더 기다려 출발
    assert stops(delayed)[1].arrive == "10:45"


def test_lodging_start_and_end() -> None:
    places = [place("A")]
    legs = [leg("@start", "A", 20), leg("A", "@end", 30)]
    common = {"startPoint": {"lat": 35.69, "lon": 139.70}, "endPoint": {"lat": 35.69, "lon": 139.70}}
    transit = compute(day([{"placeId": "A"}], places, legs, **common))
    assert stops(transit)[0].arrive == "09:20"
    assert transit.totals.move_min == 50
    assert transit.totals.end == "10:20"  # 대중교통: 종료 = 마지막 장소의 끝

    car_legs = [{**lg, "mode": "car"} for lg in legs]
    car = compute(day([{"placeId": "A"}], places, car_legs, transport="car", **common))
    assert car.totals.end == "10:50"  # 자동차: 종료 = 숙소 도착


def test_car_break_after_long_continuous_drive() -> None:
    places = [place("A", defaultStayMin=10), place("B", defaultStayMin=10), place("C")]
    legs = [leg("A", "B", 70, mode="car"), leg("B", "C", 70, mode="car")]
    r = compute(day([{"placeId": "A"}, {"placeId": "B"}, {"placeId": "C"}], places, legs, transport="car"))
    breaks = [row.break_min for row in r.rows if isinstance(row, LegRow)]
    assert breaks == [0, 15]  # 10분 체류는 휴식으로 치지 않아 연속 140분 → 두 번째 구간에 15분 휴식
    assert stops(r)[2].arrive == "11:55"  # 09:00 +10 +70 → 10:20, +10 +70 +15 → 11:55


def test_over_day_end() -> None:
    places = [place("A", defaultStayMin=600), place("B", defaultStayMin=120)]
    r = compute(day([{"placeId": "A"}, {"placeId": "B"}], places, [leg("A", "B", 10)]))
    assert r.totals.end == "21:10" and not r.totals.over_end
    r = compute(day([{"placeId": "A"}, {"placeId": "B"}], places, [leg("A", "B", 10)], dayEnd="21:00"))
    assert r.totals.over_end


# --- §5.3 근사 규칙

CITY = City(
    fare_bands=(FareBand(max_km=6, fare=180), FareBand(max_km=11, fare=210), FareBand(max_km=99, fare=330))
)


def ep(pid: str, lon: float, stations: dict[str, float] | None = None) -> Endpoint:
    return Endpoint(pid, 35.68, lon, stations)


def lc(a: Endpoint, b: Endpoint, city: City) -> tuple[int, float]:
    transfers, km = line_choice(a, b, city)
    return transfers, round(km, 3)


def at_km(km: float) -> float:
    """위도 35.68에서 경로 km(직선 × 1.3)가 km가 되는 경도 차."""
    return km / 1.3 / (111.32 * 0.8122)


@pytest.mark.parametrize(
    ("km", "mode", "minutes"),
    [
        (1.0, "walk", 14),  # 1000 / 75 = 13.3 → 14
        (1.4, "walk", 19),
        # 역까지 0.2 + 0.2km → 걷기 0.52km = 6.93분, 직통 9 + 1.8 × 3 = 14.4 → 21.33 → 22
        (3.0, "transit", 22),
    ],
)
def test_balance_mode(km: float, mode: str, minutes: int) -> None:
    a, b = ep("a", 139.70, {"L1": 0.2}), ep("b", 139.70 + at_km(km), {"L1": 0.2})
    assert route_km(a, b) == pytest.approx(km, abs=0.01)
    r = approx_leg(a, b, "balance", "transit", CITY)
    assert (r.mode, r.minutes) == (mode, minutes)


def test_cheap_mode_walks_further() -> None:
    a, b = ep("a", 139.70, {"L1": 0.2}), ep("b", 139.70 + at_km(2.5), {"L1": 0.2})
    assert approx_leg(a, b, "balance", "transit", CITY).mode == "transit"
    assert approx_leg(a, b, "cheap", "transit", CITY).mode == "walk"


def test_transfers_and_fare() -> None:
    city = City(line_links=(("L1", "L2"),), fare_bands=CITY.fare_bands)
    a = ep("a", 139.70, {"L1": 0.1})
    assert lc(a, ep("b", 139.75, {"L1": 0.5, "L3": 0.1}), city) == (0, 0.6)
    assert lc(a, ep("b", 139.75, {"L2": 0.3}), city) == (1, 0.4)
    assert lc(a, ep("b", 139.75, {"L9": 0.3}), city) == (2, 0.4)
    b = ep("b", 139.70 + at_km(8.0), {"L2": 0.3})
    r = approx_leg(a, b, "balance", "transit", city)
    # 걷기 0.4 × 1.3 = 0.52km → 6.93분, 환승 1회 20 + 1.8 × 8 = 34.4 → 41.33 → 42
    assert (r.minutes, r.fare, r.transfers) == (42, 210, 1)


def test_line_choice_prefers_closest_stations() -> None:
    city = City(line_links=(("L1", "L2"), ("L3", "L2")))
    a = ep("a", 139.70, {"L1": 0.7, "L3": 0.2})
    assert lc(a, ep("b", 139.75, {"L2": 0.1}), city) == (1, 0.3)


def test_station_distance_when_unknown_or_far() -> None:
    # 가까운 역이 없으면(빈 값) 한쪽 0.8km, 역 정보가 없는 지점(None)은 0.35km로 본다
    assert lc(ep("a", 139.70, {}), ep("b", 139.75, {}), City()) == (2, 1.6)
    assert lc(ep("a", 139.70), ep("b", 139.75, {"L1": 0.1}), City()) == (2, 0.45)


def test_fast_mode_takes_taxi_for_long_transfers() -> None:
    no_taxi = City(line_links=(("L1", "L2"),), fare_bands=CITY.fare_bands)
    city = no_taxi.model_copy(update={"taxi": TaxiFare(base=500, base_km=1.0, per_km=431)})
    a, b = ep("a", 139.70, {"L1": 0.1}), ep("b", 139.70 + at_km(8.0), {"L2": 0.3})
    r = approx_leg(a, b, "fast", "transit", city)
    assert (r.mode, r.minutes) == ("taxi", 28)  # 8 + 2.4 × 8 = 27.2 → 28 < 대중교통 42
    assert (r.km, r.fare) == (7.99, 500 + 3013)  # 1km까지 500엔 + 6.99km × 431엔(올림)
    # 택시 운임이 없는 도시는 택시를 후보로 쓰지 않는다(기본 운임을 지어내지 않는다)
    assert approx_leg(a, b, "fast", "transit", no_taxi).mode == "transit"
    near = ep("c", 139.70 + at_km(5.0), {"L2": 0.3})
    assert approx_leg(a, near, "fast", "transit", city).mode == "transit"  # 6km 이하는 비교하지 않는다


def test_car_leg_includes_parking_minutes() -> None:
    a, b = ep("a", 139.70), ep("b", 139.70 + at_km(30.0))
    r = approx_leg(a, b, "balance", "car", City(fuel_per_km=12), parking_fee=800)
    # 경로 29.97km: 8 + 2.4×20 + 0.9×9.97 = 64.97 → 65분 + 주차 10분
    assert r.minutes == 75
    assert r.fare == 360 + 800  # 유류비 29.97 × 12 = 359.6 → 360, 주차비 800

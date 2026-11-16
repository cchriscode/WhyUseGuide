from typing import Any

from itinerary_engine.config import to_min
from itinerary_engine.generate import GenerateInput, generate, kmeans, taste_match
from itinerary_engine.models import StopRow

D1, D2 = "2026-10-27", "2026-10-28"
WEST = (35.690, 139.700)
EAST = (35.712, 139.796)


def spot(pid: str, base: tuple[float, float], i: int, **kw: Any) -> dict[str, Any]:
    """기준점 주변 약 300m 간격 격자에 놓는다."""
    lat, lon = base
    return {
        "id": pid,
        "name": pid,
        "type": kw.pop("type", "박물관"),
        "lat": lat + 0.0027 * (i % 3),
        "lon": lon + 0.0033 * (i // 3),
        "defaultStayMin": kw.pop("stay", 60),
        "rating": kw.pop("rating", 4.0),
        "open": "09:00",
        "close": "21:00",
        **kw,
    }


def make_input(**over: Any) -> GenerateInput:
    west = [spot(f"W{i}", WEST, i, rating=4.0 + i / 100) for i in range(8)]
    east = [spot(f"E{i}", EAST, i, rating=4.0 + i / 100) for i in range(8)]
    west[0]["type"] = "맛집"
    east[0]["type"] = "맛집"
    places = west + east
    profiles = {p["id"]: {"tags": {"역사·문화": 1.0}, "popularity": 3} for p in places}
    per_date = {D1: {p["id"]: dict(p) for p in places}, D2: {p["id"]: dict(p) for p in places}}
    data: dict[str, Any] = {
        "dates": [D1, D2],
        "tastes": ["역사·문화"],
        "places": per_date,
        "profiles": profiles,
        "pace": "relaxed",
        **over,
    }
    return GenerateInput.model_validate(data)


def ids(day: Any) -> list[str]:
    return [it.place_id for it in day.items]


def stops(result: Any) -> list[StopRow]:
    return [r for r in result.rows if isinstance(r, StopRow)]


def test_taste_match() -> None:
    assert taste_match(("맛집",), {}, {"맛집": 1.0}) == 1.0
    assert taste_match(("맛집",), {}, {"쇼핑": 1.0}) == 0.0
    assert taste_match(("맛집",), {"맛집": -1.0}, {"맛집": 1.0}) == 0.0  # 평가로 가중치가 0이 되면 일치 없음


def test_kmeans_is_deterministic_and_separates_areas() -> None:
    pts = {"a": (0.0, 0.0), "b": (0.1, 0.0), "c": (10.0, 0.0), "d": (10.1, 0.0)}
    first = kmeans(pts, ["a", "b", "c", "d"], 2)
    assert first == kmeans(pts, ["a", "b", "c", "d"], 2)
    assert first["a"] == first["b"] != first["c"] == first["d"]


def test_each_day_stays_in_one_area_with_pace_count() -> None:
    result = generate(make_input())
    assert len(result.days) == 2
    areas = []
    for day in result.days:
        assert len(day.items) == 4  # 여유 = 하루 4곳
        prefixes = {pid[0] for pid in ids(day)}
        assert len(prefixes) == 1, ids(day)
        areas.append(prefixes.pop())
    assert sorted(areas) == ["E", "W"]
    assert not set(ids(result.days[0])) & set(ids(result.days[1]))


def test_lunch_place_starts_around_noon() -> None:
    result = generate(make_input())
    for day in result.days:
        lunch = next(pid for pid in ids(day) if pid.endswith("0"))  # W0·E0 = 맛집
        row = next(s for s in stops(day.plans["balance"]) if s.place_id == lunch)
        assert 11 * 60 <= to_min(row.start) <= 13 * 60


def test_fixed_booking_and_closed_day() -> None:
    inp = make_input(fixed=[{"placeId": "E5", "date": D1, "time": "15:00"}])
    inp.places[D1]["W3"] = inp.places[D1]["W3"].model_copy(update={"closed": True})
    inp.places[D2]["W3"] = inp.places[D2]["W3"].model_copy(update={"closed": True})
    result = generate(inp)
    d1 = result.days[0]
    assert "E5" in ids(d1)
    row = next(s for s in stops(d1.plans["balance"]) if s.place_id == "E5")
    assert row.start == "15:00" and row.issue is None
    assert all("W3" not in ids(d) for d in result.days)


def test_three_plans_share_places_and_are_deterministic() -> None:
    a = generate(make_input())
    b = generate(make_input())
    assert a == b
    for day in a.days:
        assert set(day.plans) == {"fast", "balance", "cheap"}
        for plan in day.plans.values():
            assert {s.place_id for s in stops(plan)} == set(ids(day))


def test_far_place_is_swapped_for_a_closer_one_when_move_cap_exceeded() -> None:
    # 서쪽 묶음에 동떨어진 고득점 장소(X)를 넣고, 이동 상한을 아주 낮게 잡는다.
    inp = make_input(cityMoveCapFactor=0.3)
    far = spot("WX", (35.640, 139.700), 0, rating=4.9)
    for date in (D1, D2):
        inp.places[date]["WX"] = GenerateInput.model_validate(
            {"dates": [], "tastes": [], "places": {"x": {"WX": far}}, "profiles": {}}
        ).places["x"]["WX"]
    inp.profiles["WX"] = inp.profiles["W1"]
    result = generate(inp)
    west_day = next(d for d in result.days if any(p.startswith("W") for p in ids(d)))
    assert "WX" not in ids(west_day)
    assert west_day.swapped and west_day.swapped[0][0] == "WX"


def test_crowd_avoid_moves_famous_places_to_morning() -> None:
    base = make_input(mix=0)  # 유명한 곳 선호 → 유명도 5인 장소가 확실히 뽑힌다
    famous = "W7"
    base.profiles[famous] = base.profiles[famous].model_copy(update={"popularity": 5})
    plain = generate(base)
    west = next(d for d in plain.days if famous in ids(d))
    late_start = next(s for s in stops(west.plans["balance"]) if s.place_id == famous).start

    avoid = generate(base.model_copy(update={"prefs": base.prefs.model_copy(update={"crowd_avoid": True})}))
    west_avoid = next(d for d in avoid.days if famous in ids(d))
    start = next(s for s in stops(west_avoid.plans["balance"]) if s.place_id == famous).start
    assert to_min(start) <= 12 * 60
    assert to_min(start) <= to_min(late_start)

import json
from datetime import date

import httpx
import pytest

from whyuseguide_api.otp import OtpError
from whyuseguide_pipelines.benchmark import (
    SEGMENTS,
    OtpPlanner,
    Spot,
    Summary,
    departures,
    metrics,
    sample_pairs,
    segment,
)


def test_metrics() -> None:
    results = [
        (Summary(30, 1, "r1"), Summary(28, 1, "r1")),  # +2
        (Summary(20, 0, "r2"), Summary(30, 1, "r3")),  # −10
        (Summary(15, 0, None), Summary(14, 0, None)),  # +1, 걷기만
        (None, Summary(10, 0, "r1")),
        (Summary(10, 0, "r1"), None),
    ]
    m = metrics(results)
    assert (m.pairs, m.failed_ours, m.failed_ref, m.compared) == (5, 1, 1, 3)
    assert m.median_abs_diff_min == 2
    assert m.within_5_min_pct == 66.7
    assert m.within_10_min_pct == 100.0
    assert m.mean_diff_min == -2.3
    assert m.transfers_match_pct == 66.7
    assert m.first_route_match_pct == 50.0  # 노선이 있는 2건 중 1건


def test_metrics_empty() -> None:
    m = metrics([])
    assert m.compared == 0 and m.median_abs_diff_min is None


def spots() -> list[Spot]:
    return [Spot(f"s{i}", f"s{i}", 35.65 + 0.01 * (i % 7), 139.65 + 0.015 * (i // 7)) for i in range(40)]


def test_sample_pairs_is_fixed_by_seed_and_respects_distance() -> None:
    a = sample_pairs(spots(), 30, seed=1)
    b = sample_pairs(list(reversed(spots())), 30, seed=1)
    assert a == b  # 입력 순서와 무관하게 같은 표본
    assert len(a) == 30
    assert len({(x.id, y.id) for x, y in a}) == 30
    assert sample_pairs(spots(), 30, seed=2) != a


def test_segment_separates_real_timetables_from_estimates() -> None:
    near_a, near_b = Spot("a", "a", 35.7, 139.7, {"L1": 0.2}), Spot("b", "b", 35.7, 139.8, {"L1": 0.3})
    far = Spot("c", "c", 35.7, 139.9)
    assert segment(near_a, near_b, Summary(30, 0, "r1")) == SEGMENTS[0]
    assert segment(near_a, near_b, Summary(30, 0, "jr", estimated=True)) == SEGMENTS[1]
    assert segment(near_a, far, Summary(30, 0, "r1")) == SEGMENTS[2]


def test_departures_follow_the_base_date() -> None:
    """기준일 이후 첫 화요일(08:30·13:00·18:00)과 첫 토요일(11:00). 기준일이 그 요일이면 그날."""
    assert departures(date(2026, 11, 16)) == (  # 월요일
        ("2026-11-17", "08:30"),
        ("2026-11-17", "13:00"),
        ("2026-11-17", "18:00"),
        ("2026-11-21", "11:00"),
    )
    assert {d for d, _ in departures(date(2026, 11, 17))} == {"2026-11-17", "2026-11-21"}
    assert {d for d, _ in departures(date(2026, 11, 18))} == {"2026-11-24", "2026-11-21"}


def test_otp_planner_summarizes_the_shortest_itinerary() -> None:
    def itinerary(minutes: int, agencies: list[str]) -> dict[str, object]:
        legs: list[dict[str, object]] = [{"mode": "WALK", "distance": 300.0, "route": None}]
        legs += [
            {"mode": "RAIL", "distance": 2000.0, "route": {"gtfsId": f"1:{a}", "agency": {"name": a}}}
            for a in agencies
        ]
        return {"duration": minutes * 60, "numberOfTransfers": max(0, len(agencies) - 1), "legs": legs}

    seen: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["variables"])
        its = [itinerary(40, ["metro"]), itinerary(31, ["metro", "jr"])]
        return httpx.Response(200, json={"data": {"plan": {"routingErrors": [], "itineraries": its}}})

    a, b = Spot("a", "A", 35.70, 139.70), Spot("b", "B", 35.72, 139.76)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        planner = OtpPlanner(
            client, "http://otp", banned_agencies="1:x", estimated_agencies=frozenset({"jr"})
        )
        assert planner(a, b, "2026-11-17", "08:30") == Summary(31, 1, "1:metro", estimated=True)
    assert seen[0]["banned"] == "1:x" and seen[0]["date"] == "2026-11-17" and seen[0]["n"] == 3


def test_otp_planner_stops_the_benchmark_on_failure() -> None:
    """기준 경로를 못 받으면 조용히 빼지 않고 멈춘다(빠진 채로 집계하면 결과가 달라진다)."""
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        with pytest.raises(OtpError, match="HTTP 500"):
            OtpPlanner(client, "http://otp")(
                Spot("a", "A", 35.7, 139.7), Spot("b", "B", 35.72, 139.76), "d", "t"
            )

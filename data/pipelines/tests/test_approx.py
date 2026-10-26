import math
from datetime import date
from pathlib import Path

import pytest

from whyuseguide_pipelines.approx import (
    ApproxLine,
    Headway,
    Trip,
    build_feed,
    build_trips,
    load_lines,
    resolve_station,
    segment_minutes,
)
from whyuseguide_pipelines.gtfs import read_network, validate_feed
from whyuseguide_pipelines.sources import PIPELINES_DIR

OP = "テスト鉄道"


def ring(n: int) -> list[tuple[float, float]]:
    """반지름 약 5km 원 위에 같은 간격으로 놓인 역."""
    return [
        (139.7 + 0.05 * math.cos(2 * math.pi * i / n), 35.7 + 0.045 * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]


def make_line(n: int, loop: bool, every: int, total: float = 60) -> ApproxLine:
    return ApproxLine(
        id="t",
        operator=OP,
        operator_en="Test Rail",
        name_local="テスト線",
        name_en="Test Line",
        color="#123456",
        loop=loop,
        total_minutes=total,
        stations=[(f"駅{i}", f"Eki{i}") for i in range(n)],
        headways=[Headway(9 * 60, 12 * 60, every)],
    )


def test_real_definitions_load() -> None:
    lines = {ln.id: ln for ln in load_lines(PIPELINES_DIR / "approx_lines.toml")}
    yamanote = lines["jr-yamanote"]
    assert yamanote.loop
    assert len(yamanote.stations) == 30


def test_resolve_station_averages_platforms() -> None:
    index = {(OP, "東京"): [(139.766, 35.681), (139.768, 35.682)]}
    assert resolve_station(index, OP, "東京") == pytest.approx((139.767, 35.6815))


def test_resolve_station_rejects_far_namesakes() -> None:
    index = {(OP, "大塚"): [(139.73, 35.73), (139.30, 35.70)]}
    with pytest.raises(ValueError, match="떨어져"):
        resolve_station(index, OP, "大塚")
    with pytest.raises(KeyError):
        resolve_station(index, OP, "なし")


def test_segment_minutes_sum_to_total() -> None:
    pts = ring(6)
    loop = segment_minutes(pts, 60, loop=True)
    assert len(loop) == 6 and sum(loop) == pytest.approx(60)
    line = segment_minutes(pts, 30, loop=False)
    assert len(line) == 5 and sum(line) == pytest.approx(30)


def arrivals_at(trips: list[Trip], stop: str, prefix: str) -> list[float]:
    return sorted(m for t in trips if t.trip_id.startswith(prefix) for sid, m in t.stops[:-1] if sid == stop)


@pytest.mark.parametrize("every", [3, 4, 5, 6])
def test_loop_trains_are_evenly_spaced(every: int) -> None:
    n = 10
    line = make_line(n, loop=True, every=every)
    ids = [f"s{i}" for i in range(n)]
    trips = build_trips(line, ids, segment_minutes(ring(n), 60, loop=True))
    for stop in ("s0", "s3", "s5", "s8"):
        times = [t for t in arrivals_at(trips, stop, "t:outer") if 10 * 60 <= t <= 11 * 60]
        gaps = {round(b - a, 3) for a, b in zip(times, times[1:], strict=False)}
        assert gaps == {every}, (stop, gaps)


def test_loop_half_loop_trips_need_no_transfer() -> None:
    """반 바퀴 이내 구간은 같은 열차로 갈 수 있다. 그보다 길면 반대 방향이 빠르다."""
    n = 8
    line = make_line(n, loop=True, every=5)
    ids = [f"s{i}" for i in range(n)]
    trips = build_trips(line, ids, segment_minutes(ring(n), 60, loop=True))
    for direction in ("outer", "inner"):
        sequences = [[sid for sid, _ in t.stops] for t in trips if f":{direction}" in t.trip_id]
        step = 1 if direction == "outer" else -1
        for i, a in enumerate(ids):
            for hops in range(1, n // 2 + 1):
                b = ids[(i + step * hops) % n]
                assert any(a in seq[:-1] and b in seq[seq.index(a) + 1 :] for seq in sequences), (a, b)


def test_linear_line_runs_both_ways() -> None:
    line = make_line(4, loop=False, every=10, total=12)
    ids = ["s0", "s1", "s2", "s3"]
    trips = build_trips(line, ids, segment_minutes(ring(8)[:4], 12, loop=False))
    down = [t for t in trips if ":down:" in t.trip_id]
    up = [t for t in trips if ":up:" in t.trip_id]
    assert len(down) == len(up) == 18  # 09:00~12:00, 10분 간격
    assert down[0].headsign == "駅3" and up[0].headsign == "駅0"
    assert down[0].stops[-1][1] - down[0].stops[0][1] == pytest.approx(12)


def test_build_feed_is_valid_gtfs(tmp_path: Path) -> None:
    n = 6
    line = make_line(n, loop=True, every=5)
    index = {(OP, f"駅{i}"): [p] for i, p in enumerate(ring(n))}
    out = tmp_path / "approx.gtfs.zip"
    counts = build_feed([line], index, out, date(2026, 10, 1), date(2027, 9, 30))
    assert counts["stops"] == n and counts["lines"] == 1

    assert validate_feed(out, date(2026, 10, 26)).ok
    net = read_network(out)
    assert net.lines[0].name_en == "Test Line"
    assert net.lines[0].color_hex == "#123456"
    assert {s.name_en for s in net.stops} == {f"Eki{i}" for i in range(n)}
    assert len(net.stop_lines) == n

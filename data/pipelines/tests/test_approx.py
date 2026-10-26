import csv
import io
import math
import zipfile
from datetime import date
from pathlib import Path

import pytest

from whyuseguide_pipelines.approx import (
    ApproxLine,
    Headway,
    Service,
    Trip,
    build_feed,
    build_trips,
    load_lines,
    resolve_station,
    segment_minutes,
)
from whyuseguide_pipelines.gtfs import read_network, validate_feed
from whyuseguide_pipelines.holidays import parse_holidays
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
        stations=[(f"駅{i}", f"Eki{i}") for i in range(n)],
        services=[Service("all", "", tuple(range(n)), total, [Headway(9 * 60, 12 * 60, every)])],
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
    trips = build_trips(line, ids, ring(n))
    for stop in ("s0", "s3", "s5", "s8"):
        times = [t for t in arrivals_at(trips, stop, "t:all:outer") if 10 * 60 <= t <= 11 * 60]
        gaps = {round(b - a, 3) for a, b in zip(times, times[1:], strict=False)}
        assert gaps == {every}, (stop, gaps)


def test_loop_half_loop_trips_need_no_transfer() -> None:
    """반 바퀴 이내 구간은 같은 열차로 갈 수 있다. 그보다 길면 반대 방향이 빠르다."""
    n = 8
    line = make_line(n, loop=True, every=5)
    ids = [f"s{i}" for i in range(n)]
    trips = build_trips(line, ids, ring(n))
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
    trips = build_trips(line, ids, ring(8)[:4])
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


def test_express_and_local_services(tmp_path: Path) -> None:
    """급행은 일부 역만 서고, 정차역 사이 시간은 노선 거리 비율로 나뉜다."""
    toml = tmp_path / "lines.toml"
    toml.write_text(
        """
[[line]]
id = "x"
operator = "テスト鉄道"
operator_en = "Test Rail"
name_local = "テスト線"
name_en = "Test Line"
color = "#000000"
loop = false
stations = [["A", "A"], ["B", "B"], ["C", "C"], ["D", "D"]]

[[line.services]]
id = "express"
name = "急行"
stops = ["A", "D"]
total_minutes = 10
headways = [["09:00", "10:00", 20]]

[[line.services]]
id = "local"
name = "普通"
total_minutes = 15
headways = [["09:00", "10:00", 20]]
offset_min = 10
""",
        encoding="utf-8",
    )
    line = load_lines(toml)[0]
    trips = build_trips(
        line, ["a", "b", "c", "d"], [(139.70, 35.70), (139.71, 35.70), (139.72, 35.70), (139.73, 35.70)]
    )
    express = [t for t in trips if ":express:down:" in t.trip_id]
    local = [t for t in trips if ":local:down:" in t.trip_id]
    assert [sid for sid, _ in express[0].stops] == ["a", "d"]
    assert express[0].service_name == "急行"
    assert express[0].stops[-1][1] - express[0].stops[0][1] == pytest.approx(10)
    assert [sid for sid, _ in local[0].stops] == ["a", "b", "c", "d"]
    assert local[0].stops[0][1] == 9 * 60 + 10  # offset
    assert local[0].stops[1][1] - local[0].stops[0][1] == pytest.approx(5)  # 같은 간격 역 → 15분의 1/3
    assert len(express) == len(local) == 3


def test_service_stops_must_be_on_line(tmp_path: Path) -> None:
    toml = tmp_path / "lines.toml"
    toml.write_text(
        """
[[line]]
id = "x"
operator = "o"
operator_en = "O"
name_local = "l"
name_en = "L"
color = "#000000"
loop = false
stations = [["A", "A"], ["B", "B"]]

[[line.services]]
id = "s"
stops = ["A", "Z"]
total_minutes = 5
headways = [["09:00", "10:00", 20]]
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not on line"):
        load_lines(toml)


def test_explicit_stop_times(tmp_path: Path) -> None:
    toml = tmp_path / "lines.toml"
    toml.write_text(
        """
[[line]]
id = "sky"
operator = "o"
operator_en = "O"
name_local = "l"
name_en = "L"
color = "#000000"
loop = false
stations = [["A", "A"], ["B", "B"], ["C", "C"]]

[[line.services]]
id = "s"
times = [0, 5, 41]
headways = [["09:00", "10:00", 20]]
""",
        encoding="utf-8",
    )
    line = load_lines(toml)[0]
    assert line.services[0].total_minutes == 41
    trips = build_trips(line, ["a", "b", "c"], [(139.70, 35.70), (139.80, 35.70), (139.81, 35.70)])
    down = next(t for t in trips if ":down:" in t.trip_id)
    up = next(t for t in trips if ":up:" in t.trip_id)
    assert [m - down.stops[0][1] for _, m in down.stops] == pytest.approx([0, 5, 41])
    assert [m - up.stops[0][1] for _, m in up.stops] == pytest.approx([0, 36, 41])


def test_parse_holidays() -> None:
    raw = "国民の祝日・休日月日,国民の祝日・休日名称\n2027/9/20,敬老の日\n2027/9/23,秋分の日\n".encode(
        "cp932"
    )
    assert parse_holidays(raw) == {date(2027, 9, 20): "敬老の日", date(2027, 9, 23): "秋分の日"}


def test_weekday_and_holiday_services(tmp_path: Path) -> None:
    toml = tmp_path / "lines.toml"
    toml.write_text(
        """
[[line]]
id = "c"
operator = "テスト鉄道"
operator_en = "Test Rail"
name_local = "テスト線"
name_en = "Test Line"
color = "#000000"
loop = false
stations = [["A", "A"], ["B", "B"], ["C", "C"]]

[[line.services]]
id = "rapid-weekday"
total_minutes = 10
headways = [["09:00", "10:00", 20]]
days = "weekday"

[[line.services]]
id = "rapid-holiday"
stops = ["A", "C"]
total_minutes = 8
headways = [["09:00", "10:00", 20]]
days = "holiday"
""",
        encoding="utf-8",
    )
    line = load_lines(toml)[0]
    index = {
        (OP, n): [p] for n, p in zip("ABC", [(139.70, 35.70), (139.71, 35.70), (139.72, 35.70)], strict=True)
    }
    out = tmp_path / "f.zip"
    # 2027-09-20(월) 경로의 날, 2027-09-25(토)는 원래 휴일이라 예외가 필요 없다
    build_feed(
        [line], index, out, date(2027, 9, 1), date(2027, 9, 30), {date(2027, 9, 20), date(2027, 9, 25)}
    )
    with zipfile.ZipFile(out) as zf:
        trips = list(csv.DictReader(io.StringIO(zf.read("trips.txt").decode())))
        exceptions = list(csv.DictReader(io.StringIO(zf.read("calendar_dates.txt").decode())))
        calendar = {r["service_id"]: r for r in csv.DictReader(io.StringIO(zf.read("calendar.txt").decode()))}
    assert {t["service_id"] for t in trips if "rapid-weekday" in t["trip_id"]} == {"weekday"}
    assert {t["service_id"] for t in trips if "rapid-holiday" in t["trip_id"]} == {"holiday"}
    assert calendar["weekday"]["saturday"] == "0" and calendar["holiday"]["sunday"] == "1"
    assert sorted((e["service_id"], e["date"], e["exception_type"]) for e in exceptions) == [
        ("holiday", "20270920", "1"),
        ("weekday", "20270920", "2"),
    ]
    assert validate_feed(out, date(2027, 9, 1)).ok


def test_unknown_days_rejected(tmp_path: Path) -> None:
    toml = tmp_path / "lines.toml"
    toml.write_text(
        """
[[line]]
id = "c"
operator = "o"
operator_en = "O"
name_local = "l"
name_en = "L"
color = "#000000"
loop = false
stations = [["A", "A"], ["B", "B"]]

[[line.services]]
id = "s"
total_minutes = 5
headways = [["09:00", "10:00", 20]]
days = "sunday"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="days must be"):
        load_lines(toml)

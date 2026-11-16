import csv
import io
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from whyuseguide_pipelines.gtfs import read_network, validate_feed
from whyuseguide_pipelines.odpt_timetable import build_feed, chain_runs, hop_minutes, minute

OUT, IN = "odpt.RailDirection:Outbound", "odpt.RailDirection:Inbound"


def test_minute_treats_small_hours_as_after_midnight() -> None:
    assert minute("05:45") == 345
    assert minute("00:10") == 24 * 60 + 10  # 첫차(04시 이후)보다 이른 시각은 전날 운행의 연장


def test_chain_runs_links_departures_into_trains() -> None:
    """앞 역을 떠난 뒤 곧 뒤 역을 떠나는 같은 행선의 출발이 같은 열차다. 중간 역에서 시작하는 열차도 있다."""
    departures = {
        "A": [(600, "C"), (610, "C")],
        "B": [(602, "C"), (607, "C"), (612, "C")],  # 607은 B에서 시작하는 열차
    }
    runs, broken = chain_runs(["A", "B", "C"], departures)
    assert broken == 0
    assert sorted(r.stops for r in runs) == [
        [("A", 600), ("B", 602), ("C", None)],
        [("A", 610), ("B", 612), ("C", None)],
        [("B", 607), ("C", None)],
    ]


def test_chain_runs_keeps_trains_to_different_destinations_apart() -> None:
    """같은 시각대에 떠나도 행선이 다르면 다른 열차다(도중 역 종착)."""
    departures = {"A": [(600, "B"), (601, "C")], "B": [(603, "C")]}
    runs, broken = chain_runs(["A", "B", "C"], departures)
    assert broken == 0
    assert sorted(r.stops for r in runs) == [[("A", 600), ("B", None)], [("A", 601), ("B", 603), ("C", None)]]


def test_chain_runs_counts_trains_it_cannot_follow() -> None:
    runs, broken = chain_runs(["A", "B", "C"], {"A": [(600, "C")], "B": [(640, "C")]})
    assert broken == 1  # A 600발은 B에서 이어지지 않는다(40분 뒤 출발은 다른 열차)
    assert [("A", 600)] in [r.stops for r in runs]


def station(code: str, lat: float) -> dict[str, Any]:
    return {
        "owl:sameAs": f"odpt.Station:T.T.{code}",
        "odpt:stationTitle": {"ja": f"{code}駅", "en": code, "ko": f"{code}역"},
        "geo:lat": lat,
        "geo:long": 139.77,
    }


def timetable(code: str, direction: str, calendar: str, rows: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "owl:sameAs": f"odpt.StationTimetable:T.T.{code}",
        "odpt:station": f"odpt.Station:T.T.{code}",
        "odpt:railDirection": direction,
        "odpt:calendar": f"odpt.Calendar:{calendar}",
        "odpt:stationTimetableObject": [
            {"odpt:departureTime": t, "odpt:destinationStation": [f"odpt.Station:T.T.{dest}"]}
            for t, dest in rows
        ],
    }


def test_build_feed_makes_a_valid_gtfs_with_estimated_terminal_arrivals(tmp_path: Path) -> None:
    operator = {"owl:sameAs": "odpt.Operator:T", "odpt:operatorTitle": {"ja": "テスト", "en": "Test"}}
    railway = {
        "owl:sameAs": "odpt.Railway:T.T",
        "odpt:railwayTitle": {"ja": "テスト線", "en": "Test line", "ko": "테스트선"},
        "odpt:color": "#0065A6",
        "odpt:ascendingRailDirection": OUT,
        "odpt:descendingRailDirection": IN,
        "odpt:stationOrder": [
            {"odpt:index": i + 1, "odpt:station": f"odpt.Station:T.T.{c}"} for i, c in enumerate("ABC")
        ],
    }
    timetables = []
    for cal in ("Weekday", "SaturdayHoliday"):
        timetables += [
            timetable("A", OUT, cal, [("09:00", "C"), ("23:58", "C")]),
            timetable("B", OUT, cal, [("09:02", "C"), ("00:01", "C")]),  # 자정을 넘는 열차
            timetable("C", IN, cal, [("10:00", "A")]),
            timetable("B", IN, cal, [("10:03", "A")]),
        ]
    out = tmp_path / "t.gtfs.zip"
    counts = build_feed(
        operator,
        railway,
        [station("A", 35.60), station("B", 35.61), station("C", 35.62)],
        timetables,
        [
            {
                "odpt:fromStation": "odpt.Station:T.T.A",
                "odpt:toStation": "odpt.Station:T.T.C",
                "odpt:icCardFare": 251,
                "odpt:ticketFare": 260,
            },
            {
                "odpt:fromStation": "odpt.Station:T.T.A",
                "odpt:toStation": "odpt.Station:T.T.B",
                "odpt:icCardFare": 189,
                "odpt:ticketFare": 190,
            },
        ],
        out,
        date(2026, 11, 1),
        date(2027, 10, 31),
        "Asia/Tokyo",
        "ja",
        {date(2026, 11, 23)},
    )
    assert counts == {"stops": 3, "trips": 6, "broken": 0, "fares": 2}
    assert validate_feed(out, date(2026, 11, 16)).ok
    with zipfile.ZipFile(out) as zf:
        times = list(csv.DictReader(io.StringIO(zf.read("stop_times.txt").decode())))
        exceptions = list(csv.DictReader(io.StringIO(zf.read("calendar_dates.txt").decode())))
        rules = list(csv.DictReader(io.StringIO(zf.read("fare_rules.txt").decode())))
        zones = {
            r["stop_id"]: r["zone_id"] for r in csv.DictReader(io.StringIO(zf.read("stops.txt").decode()))
        }
    # 운임: 정류장마다 요금 구역, 구역 쌍별 IC 요금
    assert zones == {"T.T.A": "T.T.A", "T.T.B": "T.T.B", "T.T.C": "T.T.C"}
    assert {(r["origin_id"], r["destination_id"], r["fare_id"]) for r in rules} == {
        ("T.T.A", "T.T.B", "189"),
        ("T.T.A", "T.T.C", "251"),
    }
    first = [t for t in times if t["trip_id"] == "T.T-weekday-outbound-000"]
    # 종점 C 도착 = B 출발 09:02 + B~C를 달리는 시간. B~C는 반대 방향(C 10:00 → B 10:03)에서 3분으로 잰다.
    assert [(t["stop_id"], t["arrival_time"]) for t in first] == [
        ("T.T.A", "09:00:00"),
        ("T.T.B", "09:02:00"),
        ("T.T.C", "09:05:00"),
    ]
    late = [t["arrival_time"] for t in times if t["trip_id"] == "T.T-weekday-outbound-001"]
    assert late == ["23:58:00", "24:01:00", "24:04:00"]
    assert ("weekday", "20261123", "2") in {
        (e["service_id"], e["date"], e["exception_type"]) for e in exceptions
    }

    network = read_network(out)
    assert {(s.name_local, s.name_ko) for s in network.stops} == {
        ("A駅", "A역"),
        ("B駅", "B역"),
        ("C駅", "C역"),
    }
    assert [(ln.name_local, ln.name_en, ln.name_ko) for ln in network.lines] == [
        ("テスト線", "Test line", "테스트선")
    ]


def test_hop_minutes_ignores_direction() -> None:
    runs, _ = chain_runs(["A", "B"], {"A": [(600, "B")]})
    back, _ = chain_runs(["B", "A", "Z"], {"B": [(700, "Z")], "A": [(704, "Z")]})
    assert hop_minutes(runs + back) == {frozenset(("A", "B")): 4}

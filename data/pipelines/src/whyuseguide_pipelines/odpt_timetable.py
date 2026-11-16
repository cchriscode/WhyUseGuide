"""ODPT 역 시간표(JSON) → GTFS. GTFS를 내지 않는 운영사(유리카모메)의 실제 시간표를 OTP에 넣기 위한 것.

역 시간표에는 역마다 "몇 시 몇 분에 어느 행선 열차가 떠난다"만 있고 열차 번호가 없다. 모든 열차가 각 역에
서고 추월이 없는 노선이라는 전제로, 앞 역을 떠난 뒤 곧 뒤 역을 떠나는 같은 행선의 출발을 같은 열차로 잇는다.
종점 도착 시각은 시간표에 없어서, 그 구간을 다른 열차가 달린 시간의 중앙값으로 정한다.
"""

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from whyuseguide_pipelines.gtfs_write import Trip, write_feed

MAX_HOP_MIN = 8  # 한 역 사이를 가는 데 걸리는 시간의 상한(같은 열차로 잇는 창). 유리카모메는 역 사이 1~3분
DAY_START_MIN = 4 * 60  # 이보다 이른 시각은 자정을 넘긴 운행(24시 이후)으로 본다
CALENDARS = {"odpt.Calendar:Weekday": "weekday", "odpt.Calendar:SaturdayHoliday": "holiday"}
ROUTE_TYPE_RAIL = "2"
PUBLISHER = "WhyUseGuide conversion of ODPT station timetables (terminal arrival times are estimated)"


def short(odpt_id: str) -> str:
    """ "odpt.Station:Yurikamome.Yurikamome.Aomi" → "Yurikamome.Yurikamome.Aomi"."""
    return odpt_id.split(":", 1)[1]


def minute(hhmm: str) -> int:
    h, m = hhmm.split(":")
    value = int(h) * 60 + int(m)
    return value + 24 * 60 if value < DAY_START_MIN else value


@dataclass
class Run:
    """한 열차가 지나간 역들. 종점은 출발 시각이 없어 None으로 두었다가 채운다."""

    destination: str
    stops: list[tuple[str, int | None]] = field(default_factory=list)

    @property
    def last_departure(self) -> int:
        last = self.stops[-1][1]
        assert last is not None
        return last


def chain_runs(order: list[str], departures: dict[str, list[tuple[int, str]]]) -> tuple[list[Run], int]:
    """한 방향·한 운행일의 역 시간표를 열차로 잇는다.

    order: 진행 방향의 역 순서. departures: 역 → [(출발 분, 행선 역)].
    반환: (열차들, 행선에 닿기 전에 끊긴 열차 수). 끊긴 열차는 이어진 데까지만 남긴다.
    """
    running: list[Run] = []
    done: list[Run] = []
    broken = 0
    for station in order:
        deps = sorted(departures.get(station, []))
        used = [False] * len(deps)
        still: list[Run] = []
        for run in sorted(running, key=lambda r: r.last_departure):
            if run.destination == station:
                run.stops.append((station, None))
                done.append(run)
                continue
            match = next(
                (
                    j
                    for j, (m, dest) in enumerate(deps)
                    if not used[j]
                    and dest == run.destination
                    and run.last_departure < m <= run.last_departure + MAX_HOP_MIN
                ),
                None,
            )
            if match is None:
                broken += 1
                done.append(run)
                continue
            used[match] = True
            run.stops.append((station, deps[match][0]))
            still.append(run)
        still += [Run(dest, [(station, m)]) for j, (m, dest) in enumerate(deps) if not used[j]]
        running = still
    broken += len(running)  # 마지막 역을 지나서도 행선에 닿지 못한 열차
    return done + running, broken


def hop_minutes(runs: list[Run]) -> dict[frozenset[str], float]:
    """이웃한 두 역 사이를 달린 시간(출발~출발)의 중앙값. 방향은 구분하지 않는다."""
    samples: dict[frozenset[str], list[int]] = defaultdict(list)
    for run in runs:
        for (a, ta), (b, tb) in zip(run.stops, run.stops[1:], strict=False):
            if ta is not None and tb is not None:
                samples[frozenset((a, b))].append(tb - ta)
    return {pair: statistics.median(v) for pair, v in samples.items()}


def build_feed(
    operator: dict[str, Any],
    railway: dict[str, Any],
    stations: list[dict[str, Any]],
    timetables: list[dict[str, Any]],
    fares: list[dict[str, Any]],
    out: Path,
    valid_from: date,
    valid_to: date,
    timezone: str,
    lang: str,
    holidays: set[date] | None = None,
) -> dict[str, int]:
    """ODPT 객체(odpt:Operator, odpt:Railway, odpt:Station[], odpt:StationTimetable[],
    odpt:RailwayFare[]) → GTFS zip. 운임은 IC 카드 요금을 쓴다(다른 운영사 GTFS와 같은 기준).
    """
    agency_id = short(operator["owl:sameAs"]).lower()
    route_id = short(railway["owl:sameAs"])
    order = [
        short(s["odpt:station"]) for s in sorted(railway["odpt:stationOrder"], key=lambda s: s["odpt:index"])
    ]
    directions = {
        railway["odpt:ascendingRailDirection"]: order,
        railway["odpt:descendingRailDirection"]: order[::-1],
    }
    by_id = {short(s["owl:sameAs"]): s for s in stations}
    missing = [s for s in order if s not in by_id]
    if missing:
        raise ValueError(f"{route_id}: 역 정보가 없는 역이 있다: {missing}")

    grouped: dict[tuple[str, str], dict[str, list[tuple[int, str]]]] = defaultdict(dict)
    for t in timetables:
        if t["odpt:calendar"] not in CALENDARS or t["odpt:railDirection"] not in directions:
            raise ValueError(f"{t.get('owl:sameAs')}: 모르는 운행일·방향이다")
        grouped[(CALENDARS[t["odpt:calendar"]], t["odpt:railDirection"])][short(t["odpt:station"])] = [
            (minute(o["odpt:departureTime"]), short(o["odpt:destinationStation"][0]))
            for o in t["odpt:stationTimetableObject"]
        ]

    chained: dict[tuple[str, str], list[Run]] = {}
    broken = 0
    for key, departures in grouped.items():
        chained[key], n = chain_runs(directions[key[1]], departures)
        broken += n
    hops = hop_minutes([r for runs in chained.values() for r in runs])
    typical = statistics.median(hops.values()) if hops else 2.0

    trips: list[Trip] = []
    for (service, direction), runs in sorted(chained.items()):
        tag = short(direction).lower()
        for n, run in enumerate(sorted(runs, key=lambda r: r.stops[0][1] or 0)):
            stops: list[tuple[str, float]] = []
            for station, m in run.stops:
                if m is None:  # 종점: 앞 역 출발 + 그 구간을 달리는 시간
                    prev, prev_m = stops[-1]
                    m = round(prev_m + hops.get(frozenset((prev, station)), typical))
                stops.append((station, m))
            if len(stops) < 2:
                continue
            trips.append(
                Trip(
                    trip_id=f"{route_id}-{service}-{tag}-{n:03d}",
                    route_id=route_id,
                    service_name="",
                    service_days=service,
                    headsign=by_id[run.destination]["odpt:stationTitle"]["ja"],
                    stops=stops,
                )
            )

    translations = []
    for sid in order:
        for language, name in by_id[sid]["odpt:stationTitle"].items():
            if language in ("en", "ko"):
                translations.append(
                    {
                        "table_name": "stops",
                        "field_name": "stop_name",
                        "language": language,
                        "translation": name,
                        "record_id": sid,
                    }
                )
    for language, name in railway["odpt:railwayTitle"].items():
        if language in ("en", "ko"):
            translations.append(
                {
                    "table_name": "routes",
                    "field_name": "route_long_name",
                    "language": language,
                    "translation": name,
                    "record_id": route_id,
                }
            )
    write_feed(
        out,
        agencies={agency_id: operator["odpt:operatorTitle"]["ja"]},
        stops=[
            {
                "stop_id": sid,
                "stop_name": by_id[sid]["odpt:stationTitle"]["ja"],
                "stop_lat": f"{by_id[sid]['geo:lat']:.6f}",
                "stop_lon": f"{by_id[sid]['geo:long']:.6f}",
            }
            for sid in order
        ],
        routes=[
            {
                "route_id": route_id,
                "agency_id": agency_id,
                "route_long_name": railway["odpt:railwayTitle"]["ja"],
                "route_type": ROUTE_TYPE_RAIL,
                "route_color": railway["odpt:color"].lstrip("#"),
            }
        ],
        trips=trips,
        translations=translations,
        valid_from=valid_from,
        valid_to=valid_to,
        timezone=timezone,
        lang=lang,
        publisher=PUBLISHER,
        holidays=holidays,
        fares=[
            (short(f["odpt:fromStation"]), short(f["odpt:toStation"]), f["odpt:icCardFare"])
            for f in fares
            if short(f["odpt:fromStation"]) in by_id and short(f["odpt:toStation"]) in by_id
        ],
    )
    return {"stops": len(order), "trips": len(trips), "broken": broken, "fares": len(fares)}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))

"""요약 사실로 근사 GTFS를 만든다 (approx_lines.toml → GTFS zip).

시간표 데이터를 상업적으로 쓸 수 없는 노선(JR·사철·공항선)을 위한 것이다.
역 간 소요 시간은 전 구간 소요 시간을 역 간 거리 비율로 나눠 정하고, 운행은 시간대별 배차 간격으로 펼친다.
순환선은 출발점이 반대편인 두 패턴을 배차 간격의 두 배로 엇갈려 돌려, 어느 구간이든 한 열차로 갈 수 있게 한다.
"""

import csv
import io
import json
import math
import tomllib
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# N02 역 지오메트리를 찾을 범위: 도쿄권(1도 ≒ 90~110km)
TOKYO_BBOX = (138.9, 35.0, 140.6, 36.3)  # 서, 남, 동, 북
SAME_STATION_KM = 1.5
# GTFS calendar: 평일(월~금), 휴일(토·일). 평일에 걸린 공휴일은 calendar_dates로 휴일 쪽에 옮긴다.
SERVICE_DAYS = {"all": "1111111", "weekday": "1111100", "holiday": "0000011"}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass(frozen=True)
class Headway:
    start_min: int  # 자정 기준 분. 24시 넘으면 1440 이상
    end_min: int
    every_min: int


@dataclass(frozen=True)
class Service:
    """열차 종별(예: エアポート快特, 普通). stops는 노선 역 목록의 인덱스."""

    id: str
    name: str
    stops: tuple[int, ...]
    total_minutes: float  # 첫 정차역 → 마지막 정차역
    headways: list[Headway]
    offset_min: int = 0  # 같은 노선의 다른 종별과 엇갈리게 출발을 미룬다
    times: tuple[float, ...] | None = None  # 정차역별 누적 분(출처가 있을 때). 없으면 거리 비율로 나눈다
    days: str = "all"  # all, weekday(평일), holiday(토·일·공휴일)


@dataclass(frozen=True)
class ApproxLine:
    id: str
    operator: str
    operator_en: str
    name_local: str
    name_en: str
    color: str
    loop: bool
    stations: list[tuple[str, str]]  # (현지어 이름, 영어 이름), 노선 순서
    services: list[Service]


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _headways(line_id: str, raw: list[list[str | int]]) -> list[Headway]:
    headways = [Headway(_minutes(str(a)), _minutes(str(b)), int(n)) for a, b, n in raw]
    for h in headways:
        if not (h.start_min < h.end_min and h.every_min > 0):
            raise ValueError(f"{line_id}: bad headway {h}")
    return headways


def load_lines(path: Path) -> list[ApproxLine]:
    """approx_lines.toml을 읽는다.

    종별이 하나뿐인 노선은 노선에 total_minutes·headways를 바로 적고, 여럿이면 [[line.services]]로 적는다.
    """
    with path.open("rb") as f:
        raw = tomllib.load(f)["line"]
    lines = []
    for r in raw:
        stations = [(ja, en) for ja, en in r["stations"]]
        names = [ja for ja, _ in stations]
        if len(set(names)) != len(names):
            raise ValueError(f"{r['id']}: duplicate station")
        specs = r.get("services") or [
            {"id": "all", "name": "", "total_minutes": r["total_minutes"], "headways": r["headways"]}
        ]
        services = []
        for spec in specs:
            stop_names = spec.get("stops", names)
            unknown = [n for n in stop_names if n not in names]
            if unknown:
                raise ValueError(f"{r['id']}/{spec['id']}: stops not on line: {unknown}")
            stops = tuple(names.index(n) for n in stop_names)
            if list(stops) != sorted(stops) or len(stops) < 2:
                raise ValueError(f"{r['id']}/{spec['id']}: stops must follow line order")
            times = tuple(float(t) for t in spec["times"]) if "times" in spec else None
            if times is not None and (
                len(times) != len(stops)
                or times[0] != 0
                or any(b <= a for a, b in zip(times[:-1], times[1:], strict=True))
            ):
                raise ValueError(f"{r['id']}/{spec['id']}: times must start at 0 and increase, one per stop")
            services.append(
                Service(
                    id=spec["id"],
                    name=spec.get("name", ""),
                    stops=stops,
                    total_minutes=times[-1] if times else float(spec["total_minutes"]),
                    headways=_headways(r["id"], spec["headways"]),
                    offset_min=int(spec.get("offset_min", 0)),
                    times=times,
                    days=spec.get("days", "all"),
                )
            )
        for svc in services:
            if svc.days not in SERVICE_DAYS:
                raise ValueError(f"{r['id']}/{svc.id}: days must be one of {sorted(SERVICE_DAYS)}")
        if r["loop"] and (len(services) != 1 or len(services[0].stops) != len(stations)):
            raise ValueError(f"{r['id']}: loop lines support one all-stop service")
        lines.append(
            ApproxLine(
                id=r["id"],
                operator=r["operator"],
                operator_en=r["operator_en"],
                name_local=r["name_local"],
                name_en=r["name_en"],
                color=r["color"],
                loop=r["loop"],
                stations=stations,
                services=services,
            )
        )
    return lines


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def station_index(n02_zip: Path) -> dict[tuple[str, str], list[tuple[float, float]]]:
    """(운영사, 역 이름) → 도쿄권 안의 역 지오메트리 중심점 목록 (N02 Station)."""
    with zipfile.ZipFile(n02_zip) as zf:
        name = next(n for n in zf.namelist() if n.endswith("UTF-8/N02-25_Station.geojson"))
        features = json.loads(zf.read(name))["features"]
    west, south, east, north = TOKYO_BBOX
    index: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for f in features:
        coords = f["geometry"]["coordinates"]
        lon = sum(c[0] for c in coords) / len(coords)
        lat = sum(c[1] for c in coords) / len(coords)
        if west <= lon <= east and south <= lat <= north:
            p = f["properties"]
            index.setdefault((p["N02_004"], p["N02_005"]), []).append((lon, lat))
    return index


def resolve_station(
    index: dict[tuple[str, str], list[tuple[float, float]]], operator: str, name: str
) -> tuple[float, float]:
    """같은 역이 노선마다 따로 있으면 평균을 쓴다. 서로 멀리 떨어진 동명 역이면 오류."""
    points = index.get((operator, name))
    if not points:
        raise KeyError(f"N02에 없는 역: {operator} {name}")
    center = (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
    far = max(_km(center, p) for p in points)
    if far > SAME_STATION_KM:
        raise ValueError(f"동명 역이 {far:.1f}km 떨어져 있음: {operator} {name}")
    return center


def segment_minutes(points: list[tuple[float, float]], total_minutes: float, loop: bool) -> list[float]:
    """역 간 소요 시간(분). 순환선은 마지막 역 → 첫 역 구간까지 포함한다."""
    pairs = (
        list(zip(points, points[1:] + points[:1], strict=True))
        if loop
        else list(zip(points[:-1], points[1:], strict=True))
    )
    distances = [_km(a, b) for a, b in pairs]
    total = sum(distances)
    return [total_minutes * d / total for d in distances]


def departures(headways: list[Headway], every_factor: int = 1) -> list[int]:
    out: list[int] = []
    for h in headways:
        t = h.start_min
        while t < h.end_min:
            if not out or t > out[-1]:
                out.append(t)
            t += h.every_min * every_factor
    return out


def headway_at(headways: list[Headway], t: float) -> int:
    for h in headways:
        if h.start_min <= t < h.end_min:
            return h.every_min
    return headways[-1].every_min


def _hms(minutes: float) -> str:
    s = round(minutes * 60)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


@dataclass
class Trip:
    trip_id: str
    route_id: str
    service_name: str
    service_days: str
    headsign: str
    stops: list[tuple[str, float]]  # (stop_id, 자정 기준 분)


def build_trips(line: ApproxLine, stop_ids: list[str], points: list[tuple[float, float]]) -> list[Trip]:
    trips: list[Trip] = []
    n = len(stop_ids)

    def run(
        svc: Service, order: list[int], segs: list[float], start: float, trip_id: str, headsign: str
    ) -> None:
        t = float(start)
        stops = [(stop_ids[order[0]], t)]
        for idx, m in zip(order[1:], segs, strict=True):
            t += m
            stops.append((stop_ids[idx], t))
        trips.append(Trip(trip_id, line.id, svc.name, svc.days, headsign, stops))

    if line.loop:
        svc = line.services[0]
        seg = segment_minutes(points, svc.total_minutes, loop=True)
        # 외선(정방향)·내선(역방향) 각각, 출발점이 반대편인 두 패턴을 엇갈려 돌린다.
        half = n // 2
        directions = {
            "outer": (list(range(n)), seg),
            "inner": (list(range(n - 1, -1, -1)), [seg[(i - 1) % n] for i in range(n - 1, -1, -1)]),
        }
        for direction, (order, segs) in directions.items():
            for k, dep in enumerate(departures(svc.headways, every_factor=2)):
                h = headway_at(svc.headways, dep)
                # 한 바퀴 시간이 2h의 배수여야 두 패턴 열차가 모든 역에서 h 간격으로 지나간다.
                # 실제 운영도 이렇게 맞추므로 시간대별 배차 간격에 맞춰 한 바퀴 시간을 늘리거나 줄인다.
                loop_minutes = 2 * h * max(1, round(svc.total_minutes / (2 * h)))
                scaled = [m * loop_minutes / svc.total_minutes for m in segs]
                # 패턴 b는 반대편(half)에서 출발해 a의 출발역을 a 열차 사이 정중앙(h 뒤)에 지나간다.
                b_dep = dep + h - sum(scaled[half:])
                for pattern, start_pos, start in (("a", 0, float(dep)), ("b", half, b_dep)):
                    if start < 0:
                        continue
                    o = order[start_pos:] + order[:start_pos]
                    s = scaled[start_pos:] + scaled[:start_pos]
                    # 한 바퀴 돌아 출발역으로 돌아온다(n개 구간).
                    run(
                        svc,
                        o + o[:1],
                        s,
                        start,
                        f"{line.id}:{svc.id}:{direction}{pattern}:{k}",
                        line.name_local,
                    )
        return trips

    # 노선을 따라 쌓은 거리. 종별의 정차역 사이 시간은 이 거리 비율로 나눈다.
    cum = [0.0]
    for a, b in zip(points[:-1], points[1:], strict=True):
        cum.append(cum[-1] + _km(a, b))
    for svc in line.services:
        stops = list(svc.stops)
        if svc.times:
            seg = [b - a for a, b in zip(svc.times[:-1], svc.times[1:], strict=True)]
        else:
            span = cum[stops[-1]] - cum[stops[0]]
            seg = [
                svc.total_minutes * (cum[b] - cum[a]) / span
                for a, b in zip(stops[:-1], stops[1:], strict=True)
            ]
        for direction, order, segs in (("down", stops, seg), ("up", stops[::-1], seg[::-1])):
            headsign = line.stations[order[-1]][0]
            for k, dep in enumerate(departures(svc.headways)):
                run(svc, order, segs, dep + svc.offset_min, f"{line.id}:{svc.id}:{direction}:{k}", headsign)
    return trips


def _csv(rows: list[dict[str, str]], fields: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def build_feed(
    lines: list[ApproxLine],
    index: dict[tuple[str, str], list[tuple[float, float]]],
    out: Path,
    valid_from: date,
    valid_to: date,
    holidays: set[date] | None = None,
) -> dict[str, int]:
    """근사 GTFS zip을 쓴다. 같은 운영사의 같은 이름 역은 노선이 달라도 정류장 하나로 둔다."""
    agencies: dict[str, str] = {}
    stops: dict[str, dict[str, str]] = {}
    translations: list[dict[str, str]] = []
    routes, all_trips = [], []
    for line in lines:
        agency_id = line.operator_en.lower().replace(" ", "-")
        agencies[agency_id] = line.operator
        stop_ids, points = [], []
        for ja, en in line.stations:
            lon, lat = resolve_station(index, line.operator, ja)
            sid = f"{agency_id}:{ja}"
            if sid not in stops:
                stops[sid] = {
                    "stop_id": sid,
                    "stop_name": ja,
                    "stop_lat": f"{lat:.6f}",
                    "stop_lon": f"{lon:.6f}",
                }
                translations.append(
                    {
                        "table_name": "stops",
                        "field_name": "stop_name",
                        "language": "en",
                        "translation": en,
                        "record_id": sid,
                    }
                )
            stop_ids.append(sid)
            points.append((lon, lat))
        routes.append(
            {
                "route_id": line.id,
                "agency_id": agency_id,
                "route_long_name": line.name_local,
                "route_type": "2",
                "route_color": line.color.lstrip("#"),
            }
        )
        translations.append(
            {
                "table_name": "routes",
                "field_name": "route_long_name",
                "language": "en",
                "translation": line.name_en,
                "record_id": line.id,
            }
        )
        all_trips += build_trips(line, stop_ids, points)

    # 평일(월~금)에 걸린 공휴일만 예외로 둔다. 토·일 공휴일은 원래 휴일 운행이다.
    weekday_holidays = {d for d in (holidays or set()) if valid_from <= d <= valid_to and d.weekday() < 5}
    files = {
        "agency.txt": _csv(
            [
                {
                    "agency_id": a,
                    "agency_name": name,
                    "agency_url": "https://example.invalid/",
                    "agency_timezone": "Asia/Tokyo",
                }
                for a, name in agencies.items()
            ],
            ["agency_id", "agency_name", "agency_url", "agency_timezone"],
        ),
        "stops.txt": _csv(list(stops.values()), ["stop_id", "stop_name", "stop_lat", "stop_lon"]),
        "routes.txt": _csv(routes, ["route_id", "agency_id", "route_long_name", "route_type", "route_color"]),
        "calendar.txt": _csv(
            [
                {
                    "service_id": sid,
                    **dict(zip(WEEKDAYS, pattern, strict=True)),
                    "start_date": valid_from.strftime("%Y%m%d"),
                    "end_date": valid_to.strftime("%Y%m%d"),
                }
                for sid, pattern in SERVICE_DAYS.items()
            ],
            ["service_id", *WEEKDAYS, "start_date", "end_date"],
        ),
        "calendar_dates.txt": _csv(
            [
                {"service_id": sid, "date": d.strftime("%Y%m%d"), "exception_type": kind}
                for d in sorted(weekday_holidays)
                for sid, kind in (("weekday", "2"), ("holiday", "1"))
            ],
            ["service_id", "date", "exception_type"],
        ),
        "trips.txt": _csv(
            [
                {
                    "route_id": t.route_id,
                    "service_id": t.service_days,
                    "trip_id": t.trip_id,
                    "trip_headsign": t.headsign,
                    "trip_short_name": t.service_name,
                }
                for t in all_trips
            ],
            ["route_id", "service_id", "trip_id", "trip_headsign", "trip_short_name"],
        ),
        "stop_times.txt": _csv(
            [
                {
                    "trip_id": t.trip_id,
                    "arrival_time": _hms(m),
                    "departure_time": _hms(m),
                    "stop_id": sid,
                    "stop_sequence": str(i + 1),
                }
                for t in all_trips
                for i, (sid, m) in enumerate(t.stops)
            ],
            ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
        ),
        "translations.txt": _csv(
            translations, ["table_name", "field_name", "language", "translation", "record_id"]
        ),
        "feed_info.txt": _csv(
            [
                {
                    "feed_publisher_name": "WhyUseGuide approximation (estimated, not an official timetable)",
                    "feed_publisher_url": "https://example.invalid/",
                    "feed_lang": "ja",
                    "feed_start_date": valid_from.strftime("%Y%m%d"),
                    "feed_end_date": valid_to.strftime("%Y%m%d"),
                }
            ],
            ["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_start_date", "feed_end_date"],
        ),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return {"lines": len(routes), "stops": len(stops), "trips": len(all_trips)}

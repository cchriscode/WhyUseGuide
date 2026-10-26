import csv
import io
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

REQUIRED_FILES = ("agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt")


@dataclass
class FeedReport:
    path: Path
    routes: int = 0
    stops: int = 0
    trips: int = 0
    service_end: date | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _rows(zf: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    with zf.open(name) as raw:
        return list(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")))


def _yyyymmdd(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _service_end(zf: zipfile.ZipFile, names: set[str]) -> date | None:
    ends: list[date] = []
    if "feed_info.txt" in names:
        ends += [_yyyymmdd(r["feed_end_date"]) for r in _rows(zf, "feed_info.txt") if r.get("feed_end_date")]
    if "calendar.txt" in names:
        ends += [_yyyymmdd(r["end_date"]) for r in _rows(zf, "calendar.txt") if r.get("end_date")]
    return max(ends) if ends else None


def validate_feed(path: Path, today: date, warn_within_days: int = 30) -> FeedReport:
    """OTP에 넣기 전 최소 점검: 필수 파일, 행 수, 서비스 종료일."""
    report = FeedReport(path=path)
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        missing = [n for n in REQUIRED_FILES if n not in names]
        if missing:
            report.errors.append(f"missing files: {', '.join(missing)}")
            return report
        if "calendar.txt" not in names and "calendar_dates.txt" not in names:
            report.errors.append("missing files: calendar.txt or calendar_dates.txt")
        report.routes = len(_rows(zf, "routes.txt"))
        report.stops = len(_rows(zf, "stops.txt"))
        report.trips = len(_rows(zf, "trips.txt"))
        report.service_end = _service_end(zf, names)
    for label, count in (("routes", report.routes), ("stops", report.stops), ("trips", report.trips)):
        if count == 0:
            report.errors.append(f"{label}.txt has no rows")
    if report.service_end is None:
        report.warnings.append("service end date unknown")
    elif report.service_end < today:
        report.errors.append(f"feed expired on {report.service_end}")
    elif (report.service_end - today).days <= warn_within_days:
        report.warnings.append(f"feed expires on {report.service_end}")
    return report


@dataclass(frozen=True)
class GtfsLine:
    route_id: str
    name_local: str
    name_en: str | None
    color_hex: str | None
    operator: str


@dataclass(frozen=True)
class GtfsStop:
    stop_id: str
    code: str | None
    name_local: str
    name_en: str | None
    lat: float
    lon: float
    parent_stop_id: str | None


@dataclass
class Network:
    lines: list[GtfsLine]
    stops: list[GtfsStop]
    stop_lines: set[tuple[str, str]]  # (stop_id, route_id)


# GTFS location_type: 0/빈값 = 승강장, 1 = 역.
# 출입구(2)·통로 노드(3)·승차 위치(4)는 stop_exits 단계에서 다룬다.
STOP_LOCATION_TYPES = ("", "0", "1")


def _translations(zf: zipfile.ZipFile, names: set[str], lang: str) -> dict[tuple[str, str], str]:
    """(table.field, record_id 또는 원래 값) → 번역.

    GTFS는 record_id 방식과 field_value 방식을 모두 허용한다.
    """
    if "translations.txt" not in names:
        return {}
    out: dict[tuple[str, str], str] = {}
    for r in _rows(zf, "translations.txt"):
        if r.get("language") != lang:
            continue
        key = r.get("record_id") or r.get("field_value") or ""
        out[(f"{r['table_name']}.{r['field_name']}", key)] = r["translation"]
    return out


def _color(value: str | None) -> str | None:
    value = (value or "").strip().lstrip("#")
    return f"#{value.upper()}" if len(value) == 6 else None


def read_network(path: Path) -> Network:
    """역·노선·역별 노선을 읽는다. 역별 노선은 실제 운행(stop_times)에서 뽑는다."""
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        en = _translations(zf, names, "en")
        agencies = {r.get("agency_id", ""): r["agency_name"] for r in _rows(zf, "agency.txt")}
        default_agency = next(iter(agencies.values()))
        lines = []
        for r in _rows(zf, "routes.txt"):
            name = r.get("route_long_name") or r.get("route_short_name") or r["route_id"]
            lines.append(
                GtfsLine(
                    route_id=r["route_id"],
                    name_local=name,
                    name_en=en.get(("routes.route_long_name", r["route_id"]))
                    or en.get(("routes.route_long_name", name)),
                    color_hex=_color(r.get("route_color")),
                    operator=agencies.get(r.get("agency_id", ""), default_agency),
                )
            )
        stops = [
            GtfsStop(
                stop_id=r["stop_id"],
                code=r.get("stop_code") or None,
                name_local=r["stop_name"],
                name_en=en.get(("stops.stop_name", r["stop_id"]))
                or en.get(("stops.stop_name", r["stop_name"])),
                lat=float(r["stop_lat"]),
                lon=float(r["stop_lon"]),
                parent_stop_id=r.get("parent_station") or None,
            )
            for r in _rows(zf, "stops.txt")
            if r.get("location_type", "") in STOP_LOCATION_TYPES
        ]
        route_of_trip = {r["trip_id"]: r["route_id"] for r in _rows(zf, "trips.txt")}
        stop_lines = {(r["stop_id"], route_of_trip[r["trip_id"]]) for r in _rows(zf, "stop_times.txt")}
    stop_ids = {s.stop_id for s in stops}
    return Network(lines=lines, stops=stops, stop_lines={p for p in stop_lines if p[0] in stop_ids})

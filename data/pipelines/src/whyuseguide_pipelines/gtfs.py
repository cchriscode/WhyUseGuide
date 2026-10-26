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

import argparse
import shutil
import sys
import tomllib
from collections.abc import Sequence
from datetime import date

import httpx
from sqlalchemy import create_engine

from whyuseguide_api.config import Settings
from whyuseguide_pipelines.approx import build_feed, load_lines, station_index
from whyuseguide_pipelines.fetch import fetch
from whyuseguide_pipelines.gtfs import read_network, validate_feed
from whyuseguide_pipelines.holidays import load_holidays
from whyuseguide_pipelines.load import KoreanNames, apply_korean_names, load_network
from whyuseguide_pipelines.sources import APPROX_LINES, KOREAN_NAMES, OTP_DIR, RAW_DIR, Source, load_sources


def _select(sources: list[Source], ids: Sequence[str]) -> list[Source]:
    if not ids:
        return sources
    known = {s.id: s for s in sources}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise SystemExit(f"unknown source ids: {', '.join(unknown)}")
    return [known[i] for i in ids]


def cmd_fetch(sources: list[Source]) -> int:
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for s in sources:
            if s.local:
                print(f"{s.id}: built locally (wug-data build-approx)")
                continue
            path = fetch(s, RAW_DIR, client)
            print(f"fetched {s.id}: {path.relative_to(RAW_DIR)} ({path.stat().st_size:,} bytes)")
    return 0


def cmd_validate(sources: list[Source]) -> int:
    failed = 0
    for s in (s for s in sources if s.kind == "gtfs"):
        path = RAW_DIR / s.id / s.filename
        if not path.exists():
            print(f"{s.id}: not fetched")
            failed += 1
            continue
        r = validate_feed(path, date.today())
        status = "ok" if r.ok else "FAIL"
        print(f"{s.id}: {status} routes={r.routes} stops={r.stops} trips={r.trips} until={r.service_end}")
        for msg in r.errors + r.warnings:
            print(f"  - {msg}")
        failed += 0 if r.ok else 1
    return 1 if failed else 0


def cmd_stage_otp(sources: list[Source]) -> int:
    """검증을 통과한 GTFS만 OTP 입력 폴더로 복사한다. 파일명에 'gtfs'가 들어가야 OTP가 인식한다."""
    staged = 0
    for s in (s for s in sources if s.otp):
        path = RAW_DIR / s.id / s.filename
        report = validate_feed(path, date.today())
        if not report.ok:
            print(f"{s.id}: skipped ({'; '.join(report.errors)})")
            continue
        shutil.copyfile(path, OTP_DIR / f"{s.id}.gtfs.zip")
        print(f"staged {s.id} -> infra/otp/{s.id}.gtfs.zip")
        staged += 1
    return 0 if staged else 1


def cmd_load_db(sources: list[Source]) -> int:
    """검증을 통과한 GTFS의 역·노선을 DB(stops, lines, stop_lines)에 적재한다."""
    with KOREAN_NAMES.open("rb") as f:
        edited = tomllib.load(f)
    names = KoreanNames(lines=edited.get("lines", {}), stops=edited.get("stops", {}))
    engine = create_engine(Settings().database_url)
    failed = 0
    for s in (s for s in sources if s.kind == "gtfs"):
        path = RAW_DIR / s.id / s.filename
        report = validate_feed(path, date.today())
        if not report.ok or s.city is None:
            print(f"{s.id}: skipped ({'; '.join(report.errors)})")
            failed += 1
            continue
        with engine.begin() as conn:
            r = load_network(conn, s.id, s.city, read_network(path), estimated=s.estimated)
            n = apply_korean_names(conn, s.id, names)
        print(
            f"loaded {s.id}: lines={r.lines} stops={r.stops} stop_lines={r.stop_lines} "
            f"removed lines={r.removed_lines} stops={r.removed_stops}"
        )
        print(f"  name_ko: editor={n.editor} machine={n.machine} official={n.official} missing={n.missing}")
    engine.dispose()
    return 1 if failed else 0


def cmd_build_approx(sources: list[Source]) -> int:
    """approx_lines.toml + 국토수치정보 N02 역 위치로 근사 GTFS를 만든다."""
    n02 = RAW_DIR / "mlit-n02-2025" / "N02-25_GML.zip"
    holidays_csv = RAW_DIR / "cao-holidays" / "syukujitsu.csv"
    missing = [p.name for p in (n02, holidays_csv) if not p.exists()]
    if missing:
        raise SystemExit(
            f"원자료가 없다({', '.join(missing)}): uv run wug-data fetch mlit-n02-2025 cao-holidays"
        )
    holidays = set(load_holidays(holidays_csv))
    lines = load_lines(APPROX_LINES)
    today = date.today()
    for s in (s for s in sources if s.local):
        out = RAW_DIR / s.id / s.filename
        valid_to = min(today.replace(year=today.year + 1), max(holidays))  # 공휴일이 공표된 날까지만
        counts = build_feed(lines, station_index(n02), out, today, valid_to, holidays)
        print(f"built {s.id}: lines={counts['lines']} stops={counts['stops']} trips={counts['trips']}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wug-data", description="WhyUseGuide 데이터 파이프라인")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "build-approx", "validate", "stage-otp", "load-db"):
        p = sub.add_parser(name)
        p.add_argument("ids", nargs="*", help="sources.toml의 id (생략하면 전체)")
    args = parser.parse_args(argv)
    sources = _select(load_sources(), args.ids)
    commands = {
        "fetch": cmd_fetch,
        "build-approx": cmd_build_approx,
        "validate": cmd_validate,
        "stage-otp": cmd_stage_otp,
        "load-db": cmd_load_db,
    }
    return commands[args.command](sources)


if __name__ == "__main__":
    sys.exit(main())

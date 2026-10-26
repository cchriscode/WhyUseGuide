import argparse
import shutil
import sys
from collections.abc import Sequence
from datetime import date

import httpx

from whyuseguide_pipelines.fetch import fetch
from whyuseguide_pipelines.gtfs import validate_feed
from whyuseguide_pipelines.sources import OTP_DIR, RAW_DIR, Source, load_sources


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wug-data", description="WhyUseGuide 데이터 파이프라인")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "validate", "stage-otp"):
        p = sub.add_parser(name)
        p.add_argument("ids", nargs="*", help="sources.toml의 id (생략하면 전체)")
    args = parser.parse_args(argv)
    sources = _select(load_sources(), args.ids)
    commands = {"fetch": cmd_fetch, "validate": cmd_validate, "stage-otp": cmd_stage_otp}
    return commands[args.command](sources)


if __name__ == "__main__":
    sys.exit(main())

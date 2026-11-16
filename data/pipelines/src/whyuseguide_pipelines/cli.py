import argparse
import shutil
import sys
import tomllib
from collections.abc import Sequence
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import create_engine

from whyuseguide_api.config import Settings
from whyuseguide_pipelines import benchmark
from whyuseguide_pipelines.approx import build_feed, load_lines, station_index
from whyuseguide_pipelines.fetch import fetch
from whyuseguide_pipelines.gtfs import read_network, validate_feed
from whyuseguide_pipelines.holidays import load_holidays
from whyuseguide_pipelines.load import KoreanNames, apply_korean_names, load_network
from whyuseguide_pipelines.places import load_places, load_wikidata_file
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


def cmd_load_places(sources: list[Source]) -> int:
    """장소 후보(Wikidata 등)를 places에 넣는다. 검수 전 후보는 숨김(hidden)."""
    engine = create_engine(Settings().database_url)
    for s in (s for s in sources if s.kind == "sparql"):
        path = RAW_DIR / s.id / s.filename
        if not path.exists() or s.city is None:
            print(f"{s.id}: not fetched")
            continue
        candidates = load_wikidata_file(path)
        with engine.begin() as conn:
            written, kept = load_places(conn, "wikidata", s.city, candidates)
        with_ko = sum(1 for c in candidates if c.name_ko)
        print(f"loaded {s.id}: candidates={len(candidates)} written={written} kept_verified={kept}")
        print(f"  name_ko from Wikidata: {with_ko}/{len(candidates)}")
    engine.dispose()
    return 0


def cmd_benchmark(city: str, pairs: int, seed: int) -> int:
    """경로 정확도 벤치마크(PRD §8). 결과는 data/raw/benchmarks/<시각>.json."""
    settings = Settings()
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        spots = benchmark.load_spots(conn, city)
        city_model = benchmark.load_city(conn, city)
    engine.dispose()
    sample = benchmark.sample_pairs(spots, pairs, seed)
    approx_operators = sorted({ln.operator for ln in load_lines(APPROX_LINES)})
    with httpx.Client(timeout=60) as client:
        agencies = client.post(
            f"{settings.otp_url}/otp/gtfs/v1", json={"query": "{ agencies { gtfsId name } }"}
        ).json()["data"]["agencies"]
        banned = ",".join(a["gtfsId"] for a in agencies if a["name"] in approx_operators)
        estimated = frozenset(approx_operators)
        otp = benchmark.OtpPlanner(client, settings.otp_url, estimated_agencies=estimated)
        otp_free_only = benchmark.OtpPlanner(
            client, settings.otp_url, banned_agencies=banned, estimated_agencies=estimated
        )
        reports = []
        for name, ours, ref in (
            ("otp-vs-approx (우리=엔진 근사식, 기준=OTP)", benchmark.approx_planner(city_model), otp),
            ("free-data-impact (우리=메트로·도에이만, 기준=근사 노선 포함)", otp_free_only, otp),
        ):
            m, detail, segments = benchmark.run(sample, ours, ref)
            reports.append(benchmark.report(name, m, detail, segments))
            print(name)
            for key, value in vars(m).items():
                print(f"  {key}: {value}")
            for seg, sm in segments.items():
                print(
                    f"  [{seg}] n={sm.compared} median={sm.median_abs_diff_min} "
                    f"within5={sm.within_5_min_pct}% within10={sm.within_10_min_pct}% mean={sm.mean_diff_min}"
                )
    out = RAW_DIR / "benchmarks" / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    benchmark.save(out, reports)
    print(f"saved {out.relative_to(RAW_DIR)} (pairs={len(sample)})")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wug-data", description="WhyUseGuide 데이터 파이프라인")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "build-approx", "validate", "stage-otp", "load-db", "load-places"):
        p = sub.add_parser(name)
        p.add_argument("ids", nargs="*", help="sources.toml의 id (생략하면 전체)")
    bench = sub.add_parser("benchmark", help="경로 정확도 벤치마크(PRD §8)")
    bench.add_argument("--city", default="tokyo")
    bench.add_argument("--pairs", type=int, default=200)
    bench.add_argument("--seed", type=int, default=20261026)
    args = parser.parse_args(argv)
    if args.command == "benchmark":
        return cmd_benchmark(args.city, args.pairs, args.seed)
    sources = _select(load_sources(), args.ids)
    commands = {
        "fetch": cmd_fetch,
        "build-approx": cmd_build_approx,
        "validate": cmd_validate,
        "stage-otp": cmd_stage_otp,
        "load-db": cmd_load_db,
        "load-places": cmd_load_places,
    }
    return commands[args.command](sources)


if __name__ == "__main__":
    sys.exit(main())

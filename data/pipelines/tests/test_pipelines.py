import hashlib
import json
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from support import make_feed

from whyuseguide_pipelines import cli
from whyuseguide_pipelines.fetch import fetch, provenance_path
from whyuseguide_pipelines.gtfs import stage_for_otp, validate_feed
from whyuseguide_pipelines.places import WIKIDATA_TYPES
from whyuseguide_pipelines.sources import PIPELINES_DIR, Source, load_sources

GOOD_FEED = {
    "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\ntoei,東京都交通局,https://x,Asia/Tokyo\n",
    "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n1,浅草,35.71,139.79\n2,新宿,35.69,139.70\n",
    "routes.txt": "route_id,agency_id,route_long_name,route_type\n1,toei,浅草線,1\n",
    "trips.txt": "route_id,service_id,trip_id\n1,0,t1\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
    "t1,09:00:00,09:00:00,1,1\nt1,09:20:00,09:20:00,2,2\n",
    "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
    "start_date,end_date\n0,1,1,1,1,1,0,0,20260314,20270312\n",
}


def test_registry_loads() -> None:
    sources = load_sources()
    assert {s.id for s in sources} >= {"toei-train-gtfs", "mlit-n02-2025"}
    assert all(s.license and s.attribution for s in sources)
    bus = next(s for s in sources if s.id == "toei-bus-gtfs")
    assert bus.otp and not bus.db  # 버스는 OTP에만(근사식의 가까운 역은 철도 기준)


def test_registry_rejects_duplicate_ids(tmp_path: Path) -> None:
    entry = 'id="a"\nkind="gtfs"\nurl="https://x/a.zip"\nlicense="CC0"\nattribution="x"\nrefresh="weekly"\notp=true\n'
    path = tmp_path / "sources.toml"
    path.write_text(f"[[source]]\n{entry}\n[[source]]\n{entry}", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_sources(path)


def test_valid_feed(tmp_path: Path) -> None:
    report = validate_feed(make_feed(tmp_path, GOOD_FEED), today=date(2026, 10, 26))
    assert report.ok
    assert (report.routes, report.stops, report.trips) == (1, 2, 1)
    assert report.service_end == date(2027, 3, 12)
    assert report.warnings == []


def test_service_period_and_unreadable_feeds(tmp_path: Path) -> None:
    """운행 시작일도 읽는다. 없는 파일·깨진 zip은 예외가 아니라 오류로 보고한다."""
    report = validate_feed(make_feed(tmp_path, GOOD_FEED), today=date(2026, 10, 26))
    assert report.service_start is not None and report.service_start <= date(2026, 10, 26)
    assert validate_feed(tmp_path / "nope.zip", today=date(2026, 10, 26)).errors == ["not fetched"]
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip")
    assert validate_feed(broken, today=date(2026, 10, 26)).errors == ["not a zip file"]


def test_out_of_service_reports_feeds_that_do_not_run_on_the_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """벤치마크 날짜가 운행 기간 밖이면 그 노선이 조용히 빠진다(2026-11-16 도에이 버스). 미리 막는다."""
    src = Source("feed", "gtfs", "https://x/feed.zip", "CC0", "x", "weekly", otp=True, city="tokyo")
    (tmp_path / "feed").mkdir()
    make_feed(tmp_path / "feed", GOOD_FEED)
    monkeypatch.setattr(cli, "RAW_DIR", tmp_path)
    start = validate_feed(tmp_path / "feed" / "feed.zip", date(2026, 10, 26)).service_start
    assert start is not None
    assert cli.out_of_service([src], [start], date(2026, 10, 26)) == []
    problems = cli.out_of_service([src], [start - timedelta(days=1), date(2030, 1, 1)], date(2026, 10, 26))
    assert len(problems) == 2 and all(p.startswith("feed: ") for p in problems)


def test_stage_for_otp_drops_only_legacy_translations(tmp_path: Path) -> None:
    """옛 GTFS-JP 번역 파일은 OTP 그래프 빌드를 멈춘다(오다큐 버스). 그 파일만 빼고 나머지는 그대로."""
    legacy = make_feed(
        tmp_path, GOOD_FEED | {"translations.txt": "trans_id,lang,translation\n浅草,en,Asakusa\n"}
    )
    out = tmp_path / "staged.zip"
    assert stage_for_otp(legacy, out) == ["translations.txt"]
    with zipfile.ZipFile(out) as zf:
        assert set(zf.namelist()) == set(GOOD_FEED)
        assert zf.read("stops.txt").decode() == GOOD_FEED["stops.txt"]
    assert validate_feed(out, today=date(2026, 10, 26)).ok

    current = "table_name,field_name,language,translation,record_id\nstops,stop_name,en,Asakusa,1\n"
    modern = make_feed(tmp_path, GOOD_FEED | {"translations.txt": current})
    assert stage_for_otp(modern, out) == []
    with zipfile.ZipFile(out) as zf:
        assert zf.read("translations.txt").decode() == current


def test_missing_required_file(tmp_path: Path) -> None:
    files = {k: v for k, v in GOOD_FEED.items() if k != "stop_times.txt"}
    report = validate_feed(make_feed(tmp_path, files), today=date(2026, 10, 26))
    assert not report.ok
    assert report.errors == ["missing files: stop_times.txt"]


def test_expired_feed_fails(tmp_path: Path) -> None:
    report = validate_feed(make_feed(tmp_path, GOOD_FEED), today=date(2027, 3, 13))
    assert report.errors == ["feed expired on 2027-03-12"]


def test_feed_close_to_expiry_warns(tmp_path: Path) -> None:
    report = validate_feed(make_feed(tmp_path, GOOD_FEED), today=date(2027, 3, 1))
    assert report.ok
    assert report.warnings == ["feed expires on 2027-03-12"]


def test_fetch_writes_file_and_provenance(tmp_path: Path) -> None:
    body = b"zip-bytes"
    source = Source(
        "toei", "gtfs", "https://example.test/data/Feed.zip?k=1", "CC BY 4.0", "東京都交通局", "weekly", True
    )
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body)))
    path = fetch(source, tmp_path, client)
    assert path == tmp_path / "toei" / "Feed.zip"
    assert path.read_bytes() == body
    record = json.loads(provenance_path(path).read_text(encoding="utf-8"))
    assert record["sha256"] == hashlib.sha256(body).hexdigest()
    assert record["license"] == "CC BY 4.0"
    assert record["url"] == source.url


def test_fetch_failure_leaves_no_file(tmp_path: Path) -> None:
    source = Source("x", "file", "https://example.test/x.zip", "CC0", "x", "weekly", False)
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(404)))
    with pytest.raises(httpx.HTTPStatusError):
        fetch(source, tmp_path, client)
    assert not (tmp_path / "x" / "x.zip").exists()


def test_fetch_sends_token_but_never_records_it(tmp_path: Path) -> None:
    seen: list[httpx.URL] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url)
        if req.url.params.get("acl:consumerKey") != "secret-token":
            return httpx.Response(403)
        return httpx.Response(200, content=b"zip")

    source = Source(
        "metro",
        "gtfs",
        "https://example.test/Metro-GTFS.zip",
        "ODPT",
        "x",
        "weekly",
        True,
        city="tokyo",
        token_env="ODPT_TOKEN",
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    path = fetch(source, tmp_path, client, token="secret-token")
    assert "secret-token" not in provenance_path(path).read_text(encoding="utf-8")
    with pytest.raises(httpx.HTTPStatusError) as err:
        fetch(source, tmp_path, client, token="wrong-token")
    assert "wrong-token" not in str(err.value)
    assert len(seen) == 2


def test_fetch_keeps_the_query_already_in_the_url(tmp_path: Path) -> None:
    """ODPT 파일 주소는 ?date=current가 붙는다. 토큰을 더할 때 그 쿼리가 사라지면 404가 난다."""
    seen: list[httpx.URL] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url)
        return httpx.Response(200, content=b"zip")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with_token = Source(
        "bus", "gtfs", "https://example.test/AllLines.zip?date=current", "ODPT", "x", "weekly", True,
        city="tokyo", token_env="ODPT_TOKEN",
    )  # fmt: skip
    path = fetch(with_token, tmp_path, client, token="secret-token")
    assert path.name == "AllLines.zip"
    assert dict(seen[-1].params) == {"date": "current", "acl:consumerKey": "secret-token"}
    public = Source(
        "pub", "gtfs", "https://example.test/Open.zip?date=current", "CC BY 4.0", "x", "weekly", True
    )
    fetch(public, tmp_path, client)
    assert dict(seen[-1].params) == {"date": "current"}


def test_source_file_name_can_be_set_when_the_url_has_none(tmp_path: Path) -> None:
    """ODPT API 주소의 마지막 부분("odpt:Station")은 파일 이름으로 쓸 수 없다(콜론)."""
    source = Source(
        "stations", "file", "https://example.test/api/v4/odpt:Station?odpt:operator=X", "ODPT", "x", "weekly",
        False, save_as="stations.json",
    )  # fmt: skip
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"[]")))
    assert fetch(source, tmp_path, client) == tmp_path / "stations" / "stations.json"


def test_registry_rejects_inputs_that_point_nowhere(tmp_path: Path) -> None:
    path = tmp_path / "sources.toml"
    path.write_text(
        """
[[source]]
id = "built"
kind = "gtfs"
url = "local:odpt-station-timetable"
license = "x"
attribution = "x"
refresh = "weekly"
otp = true
city = "tokyo"
inputs = { stations = "nope" }
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown source"):
        load_sources(path)


def test_resolve_token_reads_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from whyuseguide_pipelines.fetch import resolve_token

    monkeypatch.delenv("WUG_TEST_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("WUG_TEST_TOKEN=abc\n", encoding="utf-8")
    assert resolve_token("WUG_TEST_TOKEN", env) == "abc"
    with pytest.raises(RuntimeError):
        resolve_token("WUG_MISSING_TOKEN", env)


def test_wikidata_query_asks_for_exactly_the_classes_we_map() -> None:
    """쿼리의 분류 목록과 유형 매핑은 따로 적혀 있다. 한쪽만 고치면 받고도 버리거나 영영 못 받는다."""
    query = (PIPELINES_DIR / "queries" / "wikidata_tokyo_places.rq").read_text(encoding="utf-8")
    values = re.search(r"VALUES \?cls \{([^}]*)\}", query)
    assert values is not None
    asked = {token.removeprefix("wd:") for token in values.group(1).split()}
    assert asked == set().union(*(ids for ids, _ in WIKIDATA_TYPES))

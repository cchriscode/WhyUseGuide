import hashlib
import json
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pytest

from whyuseguide_pipelines.fetch import fetch, provenance_path
from whyuseguide_pipelines.gtfs import validate_feed
from whyuseguide_pipelines.sources import Source, load_sources

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


def make_feed(tmp_path: Path, files: dict[str, str]) -> Path:
    path = tmp_path / "feed.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return path


def test_registry_loads() -> None:
    sources = load_sources()
    assert {s.id for s in sources} >= {"toei-train-gtfs", "mlit-n02-2025"}
    assert all(s.license and s.attribution for s in sources)


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


def test_resolve_token_reads_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from whyuseguide_pipelines.fetch import resolve_token

    monkeypatch.delenv("WUG_TEST_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("WUG_TEST_TOKEN=abc\n", encoding="utf-8")
    assert resolve_token("WUG_TEST_TOKEN", env) == "abc"
    with pytest.raises(RuntimeError):
        resolve_token("WUG_MISSING_TOKEN", env)

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PIPELINES_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = PIPELINES_DIR.parents[1]
DEFAULT_REGISTRY = PIPELINES_DIR / "sources.toml"
KOREAN_NAMES = PIPELINES_DIR / "names_ko.toml"
APPROX_LINES = PIPELINES_DIR / "approx_lines.toml"
APPROX_URL = f"local:{APPROX_LINES.name}"  # 근사 노선으로 만드는 출처의 url
ODPT_TIMETABLE_URL = "local:odpt-station-timetable"  # ODPT 역 시간표로 만드는 출처의 url
CITY_FARES = PIPELINES_DIR / "city_fares.toml"
PLACE_REVIEWS_DIR = PIPELINES_DIR / "place_reviews"  # 도시별 장소 검수 자료(<도시>.json)
RAW_DIR = REPO_ROOT / "data" / "raw"
OTP_DIR = REPO_ROOT / "infra" / "otp"


@dataclass(frozen=True)
class Source:
    id: str
    kind: Literal["gtfs", "file", "sparql"]
    url: str
    license: str
    attribution: str
    refresh: str
    otp: bool
    city: str | None = None
    estimated: bool = False
    query: str | None = None  # kind = sparql: data/pipelines 기준 쿼리 파일 경로
    token_env: str | None = None  # 받을 때 필요한 토큰의 환경 변수 이름(.env). 값은 저장소에 두지 않는다.
    # 역·노선을 DB에 적재할지. 근사식(§5.3)의 "가까운 역"은 철도 기준이라 버스는 OTP에만 넣는다.
    db: bool = True
    save_as: str | None = None  # 저장할 파일 이름(주소의 마지막 부분이 파일 이름으로 못 쓰는 경우)
    # 직접 만드는 GTFS(url이 local:)의 재료: {역할: 출처 id}. 역할은 만드는 쪽(odpt_timetable)이 정한다.
    inputs: dict[str, str] | None = None

    @property
    def local(self) -> bool:
        return self.url.startswith("local:")

    @property
    def filename(self) -> str:
        if self.save_as:
            return self.save_as
        if self.local:
            return f"{self.id}.gtfs.zip"
        if self.kind == "sparql":
            return f"{self.id}.json"
        return self.url.rsplit("/", 1)[-1].split("?", 1)[0]


def load_sources(path: Path = DEFAULT_REGISTRY) -> list[Source]:
    with path.open("rb") as f:
        entries = tomllib.load(f)["source"]
    sources = [Source(**entry) for entry in entries]
    ids = [s.id for s in sources]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate source ids: {sorted(duplicates)}")
    for s in sources:
        if s.kind not in ("gtfs", "file", "sparql"):
            raise ValueError(f"{s.id}: unknown kind {s.kind!r}")
        if s.otp and s.kind != "gtfs":
            raise ValueError(f"{s.id}: only gtfs sources can feed OTP")
        if s.kind == "gtfs" and not s.city:
            raise ValueError(f"{s.id}: gtfs sources need a city")
        for role, source_id in (s.inputs or {}).items():
            if source_id not in ids:
                raise ValueError(f"{s.id}: input {role!r} refers to unknown source {source_id!r}")
        if s.kind == "sparql" and not (s.query and (PIPELINES_DIR / s.query).exists()):
            raise ValueError(f"{s.id}: sparql sources need an existing query file")
    return sources

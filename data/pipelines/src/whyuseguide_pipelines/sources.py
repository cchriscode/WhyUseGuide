import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PIPELINES_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = PIPELINES_DIR.parents[1]
DEFAULT_REGISTRY = PIPELINES_DIR / "sources.toml"
KOREAN_NAMES = PIPELINES_DIR / "names_ko.toml"
APPROX_LINES = PIPELINES_DIR / "approx_lines.toml"
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

    @property
    def local(self) -> bool:
        return self.url.startswith("local:")

    @property
    def filename(self) -> str:
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
        if s.kind == "sparql" and not (s.query and (PIPELINES_DIR / s.query).exists()):
            raise ValueError(f"{s.id}: sparql sources need an existing query file")
    return sources

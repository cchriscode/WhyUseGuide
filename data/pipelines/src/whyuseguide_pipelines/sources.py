import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PIPELINES_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = PIPELINES_DIR.parents[1]
DEFAULT_REGISTRY = PIPELINES_DIR / "sources.toml"
RAW_DIR = REPO_ROOT / "data" / "raw"
OTP_DIR = REPO_ROOT / "infra" / "otp"


@dataclass(frozen=True)
class Source:
    id: str
    kind: Literal["gtfs", "file"]
    url: str
    license: str
    attribution: str
    refresh: str
    otp: bool
    city: str | None = None

    @property
    def filename(self) -> str:
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
        if s.kind not in ("gtfs", "file"):
            raise ValueError(f"{s.id}: unknown kind {s.kind!r}")
        if s.otp and s.kind != "gtfs":
            raise ValueError(f"{s.id}: only gtfs sources can feed OTP")
        if s.kind == "gtfs" and not s.city:
            raise ValueError(f"{s.id}: gtfs sources need a city")
    return sources

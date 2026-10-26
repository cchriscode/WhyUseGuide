import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from whyuseguide_pipelines.sources import PIPELINES_DIR, Source

USER_AGENT = "WhyUseGuide-data-pipeline/0.1"


def provenance_path(target: Path) -> Path:
    return target.with_name(target.name + ".provenance.json")


def fetch(source: Source, raw_dir: Path, client: httpx.Client) -> Path:
    """원자료를 raw_dir/<id>/ 에 받고, 출처 기록(provenance)을 옆에 남긴다."""
    target = raw_dir / source.id / source.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    params: dict[str, str] = {}
    headers = {"User-Agent": USER_AGENT}
    if source.kind == "sparql" and source.query:
        params = {"query": (PIPELINES_DIR / source.query).read_text(encoding="utf-8"), "format": "json"}
        headers["Accept"] = "application/sparql-results+json"
    with client.stream("GET", source.url, params=params, headers=headers) as resp:
        resp.raise_for_status()
        with partial.open("wb") as out:
            for chunk in resp.iter_bytes():
                digest.update(chunk)
                out.write(chunk)
    partial.replace(target)
    record = {
        "source_id": source.id,
        "url": source.url,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sha256": digest.hexdigest(),
        "bytes": target.stat().st_size,
        "license": source.license,
        "attribution": source.attribution,
        **({"query": source.query} if source.query else {}),
    }
    provenance_path(target).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return target

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

from whyuseguide_pipelines.sources import PIPELINES_DIR, REPO_ROOT, Source

USER_AGENT = "WhyUseGuide-data-pipeline/0.1"
ODPT_TOKEN_PARAM = "acl:consumerKey"


def resolve_token(name: str, env_file: Path = REPO_ROOT / ".env") -> str:
    value = os.environ.get(name) or dotenv_values(env_file).get(name)
    if not value:
        raise RuntimeError(f"{name}가 없어요. 저장소 루트 .env에 {name}=... 를 넣어 주세요")
    return value


def provenance_path(target: Path) -> Path:
    return target.with_name(target.name + ".provenance.json")


def fetch(source: Source, raw_dir: Path, client: httpx.Client, token: str | None = None) -> Path:
    """원자료를 raw_dir/<id>/ 에 받고, 출처 기록(provenance)을 옆에 남긴다.

    token_env가 있는 출처(ODPT 기본 라이선스)는 토큰을 쿼리 파라미터로 붙인다.
    토큰은 출처 기록·오류 메시지에 남기지 않는다.
    """
    target = raw_dir / source.id / source.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    params: dict[str, str] = {}
    headers = {"User-Agent": USER_AGENT}
    if source.kind == "sparql" and source.query:
        params = {"query": (PIPELINES_DIR / source.query).read_text(encoding="utf-8"), "format": "json"}
        headers["Accept"] = "application/sparql-results+json"
    if source.token_env:
        params[ODPT_TOKEN_PARAM] = token or resolve_token(source.token_env)
    with client.stream("GET", source.url, params=params, headers=headers) as resp:
        if resp.is_error:
            raise httpx.HTTPStatusError(
                f"{source.id}: HTTP {resp.status_code} ({source.url})", request=resp.request, response=resp
            )
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

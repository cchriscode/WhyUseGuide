import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from whyuseguide_api.config import Settings

log = logging.getLogger(__name__)

# 각 검사는 성공하면 사람이 읽을 상세 문자열을 돌려주고, 실패하면 예외를 던진다.
Check = Callable[[], Awaitable[str]]


def build_checks(
    settings: Settings, engine: AsyncEngine, redis: Redis, http: httpx.AsyncClient
) -> dict[str, Check]:
    async def db() -> str:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SELECT postgis_lib_version()"))).scalar_one()
        return f"postgis {version}"

    async def cache() -> str:
        await redis.ping()
        return "pong"

    async def otp() -> str:
        resp = await http.get(f"{settings.otp_url}/otp/actuators/health")
        resp.raise_for_status()
        return resp.text

    return {"db": db, "redis": cache, "otp": otp}


async def run_checks(checks: dict[str, Check], timeout_s: float) -> dict[str, dict[str, object]]:
    async def one(check: Check) -> dict[str, object]:
        try:
            detail = await asyncio.wait_for(check(), timeout_s)
        except TimeoutError:
            return {"ok": False, "detail": f"timeout after {timeout_s}s"}
        except Exception as exc:  # 검사 대상마다 예외 종류가 달라 모두 실패로 기록한다.
            # /health는 인증 없이 열려 있다. 예외 문구에는 내부 주소·계정·SQL이 들어갈 수 있어
            # 응답에는 예외 종류만 싣고 문구는 로그에 남긴다.
            log.warning("health 검사 실패: %s: %s", type(exc).__name__, exc)
            return {"ok": False, "detail": type(exc).__name__}
        return {"ok": True, "detail": detail}

    results = await asyncio.gather(*(one(c) for c in checks.values()))
    return dict(zip(checks, results, strict=True))

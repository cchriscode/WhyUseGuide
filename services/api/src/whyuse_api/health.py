"""의존 서비스 헬스체크 (PRD §11 P0 완료 기준: DB·OTP·API)."""

from collections.abc import Callable
from dataclasses import dataclass

import httpx
import redis
from sqlalchemy import create_engine, text

from whyuse_api.config import Settings


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    detail: str


Check = Callable[[Settings], CheckResult]


def check_db(settings: Settings) -> CheckResult:
    engine = create_engine(
        settings.database_url,
        connect_args={"connect_timeout": int(settings.health_timeout_s)},
    )
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT postgis_lib_version()")).scalar_one()
        return CheckResult(True, f"postgis {version}")
    finally:
        engine.dispose()


def check_redis(settings: Settings) -> CheckResult:
    client = redis.Redis.from_url(
        settings.redis_url,
        socket_timeout=settings.health_timeout_s,
        socket_connect_timeout=settings.health_timeout_s,
    )
    try:
        client.ping()
        return CheckResult(True, "pong")
    finally:
        client.close()


def check_otp(settings: Settings) -> CheckResult:
    resp = httpx.get(f"{settings.otp_url}/otp/actuators/health", timeout=settings.health_timeout_s)
    status = resp.json().get("status") if resp.is_success else None
    return CheckResult(status == "UP", f"HTTP {resp.status_code} {status or ''}".strip())


DEFAULT_CHECKS: dict[str, Check] = {"db": check_db, "redis": check_redis, "otp": check_otp}


def run_checks(checks: dict[str, Check], settings: Settings) -> dict[str, CheckResult]:
    results: dict[str, CheckResult] = {}
    for name, check in checks.items():
        try:
            results[name] = check(settings)
        except Exception as exc:  # 어떤 실패든 헬스 응답에 담아 돌려준다
            results[name] = CheckResult(False, f"{type(exc).__name__}: {exc}")
    return results

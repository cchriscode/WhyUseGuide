from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Response, status
from pydantic import BaseModel

from whyuse_api.config import Settings, get_settings
from whyuse_api.health import DEFAULT_CHECKS, Check, run_checks

app = FastAPI(title="WhyUseGuide API", version="0.0.0")


class CheckOut(BaseModel):
    ok: bool
    detail: str


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, CheckOut]


def get_checks() -> dict[str, Check]:
    return DEFAULT_CHECKS


@app.get("/healthz")
def liveness() -> dict[str, str]:
    """프로세스 생존 확인. 의존 서비스는 보지 않는다."""
    return {"status": "ok"}


@app.get("/v1/health", response_model=HealthOut)
def readiness(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    checks: Annotated[dict[str, Check], Depends(get_checks)],
) -> HealthOut:
    """DB·Redis·OTP 연결 확인. 하나라도 실패하면 503."""
    results = run_checks(checks, settings)
    all_ok = all(r.ok for r in results.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthOut(
        status="ok" if all_ok else "degraded",
        checks={k: CheckOut(ok=r.ok, detail=r.detail) for k, r in results.items()},
    )

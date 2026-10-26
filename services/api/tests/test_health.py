import asyncio

from fastapi.testclient import TestClient

from whyuseguide_api.config import Settings
from whyuseguide_api.health import Check
from whyuseguide_api.main import create_app


def client_with(checks: dict[str, Check], timeout_s: float = 1.0) -> TestClient:
    app = create_app(Settings(health_timeout_s=timeout_s))
    app.state.checks = checks
    return TestClient(app)


async def ok() -> str:
    return "fine"


async def broken() -> str:
    raise ConnectionError("refused")


async def slow() -> str:
    await asyncio.sleep(5)
    return "late"


def test_all_checks_ok_returns_200() -> None:
    with client_with({"db": ok, "redis": ok, "otp": ok}) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["db"] == {"ok": True, "detail": "fine"}


def test_failing_check_returns_503_with_reason() -> None:
    with client_with({"db": ok, "redis": broken, "otp": ok}) as client:
        resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"] == {"ok": False, "detail": "ConnectionError: refused"}
    assert body["checks"]["db"]["ok"] is True


def test_slow_check_times_out() -> None:
    with client_with({"otp": slow}, timeout_s=0.1) as client:
        resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["checks"]["otp"] == {"ok": False, "detail": "timeout after 0.1s"}

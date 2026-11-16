import asyncio
import logging

import pytest
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
    assert body["checks"]["redis"] == {"ok": False, "detail": "ConnectionError"}  # 문구는 로그에만
    assert body["checks"]["db"]["ok"] is True


def test_slow_check_times_out() -> None:
    with client_with({"otp": slow}, timeout_s=0.1) as client:
        resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["checks"]["otp"] == {"ok": False, "detail": "timeout after 0.1s"}


def test_unexpected_error_uses_the_api_error_shape_and_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app(Settings())
    app.state.checks = {}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("db password is hunter2")

    with caplog.at_level(logging.ERROR, logger="whyuseguide_api"):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/boom")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"
    assert "hunter2" not in resp.text  # 원인은 응답에 싣지 않는다
    assert "hunter2" in caplog.text and "GET /boom" in caplog.text  # 로그에는 남는다


def test_failing_check_reason_is_logged_not_returned(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="whyuseguide_api"):
        with client_with({"redis": broken}) as client:
            resp = client.get("/health")
    assert "refused" not in resp.text and "refused" in caplog.text

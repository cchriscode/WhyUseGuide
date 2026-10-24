from fastapi.testclient import TestClient

from whyuse_api.config import Settings
from whyuse_api.health import CheckResult
from whyuse_api.main import app, get_checks


def _ok(_: Settings) -> CheckResult:
    return CheckResult(True, "fine")


def _boom(_: Settings) -> CheckResult:
    raise ConnectionError("refused")


client = TestClient(app)


def test_liveness() -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readiness_all_ok() -> None:
    app.dependency_overrides[get_checks] = lambda: {"db": _ok, "otp": _ok}
    try:
        resp = client.get("/v1/health")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_readiness_failure_is_503_with_detail() -> None:
    app.dependency_overrides[get_checks] = lambda: {"db": _ok, "otp": _boom}
    try:
        resp = client.get("/v1/health")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["otp"] == {"ok": False, "detail": "ConnectionError: refused"}
    assert body["checks"]["db"]["ok"] is True

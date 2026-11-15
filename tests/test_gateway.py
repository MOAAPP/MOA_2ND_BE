"""Production gateway auth and bounded AI quota, no live provider calls."""
import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(autouse=True)
def quota_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOA_REQUIRE_GATEWAY", "1")
    monkeypatch.setenv("MOA_GATEWAY_SECRET", "local-ci-only-test-token")
    monkeypatch.setenv("MOA_GATEWAY_QUOTA_DB", str(tmp_path / "quota.sqlite3"))
    monkeypatch.setenv("MOA_AI_DAILY_MAX_REQUESTS", "1")
    monkeypatch.setenv("MOA_AI_PER_CLIENT_HOURLY_MAX", "2")
    for name in ("MOA_AI_API_KEY", "MOA_AGENT_API_KEY",
                 "MOA_VISION_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_public_health_but_private_data():
    with TestClient(app) as client:
        assert client.get("/api/v4/health").status_code == 200
        assert client.get("/api/v4/users").status_code == 401
        assert client.get("/api/v4/users",
                          headers={"x-moa-gateway-key": "incorrect"}).status_code == 401
        allowed = client.get("/api/v4/users",
                             headers={"x-moa-gateway-key": "local-ci-only-test-token"})
        assert allowed.status_code == 200, allowed.text


def test_missing_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("MOA_GATEWAY_SECRET")
    with TestClient(app) as client:
        assert client.get("/api/v4/users").status_code == 503


def test_paid_endpoints_have_global_daily_cap():
    headers = {"x-moa-gateway-key": "local-ci-only-test-token"}
    with TestClient(app) as client:
        first = client.get("/api/v4/users/S01/briefing?as_of=2026-11-04", headers=headers)
        assert first.status_code == 200, first.text
        second = client.get("/api/v4/users/S01/briefing?as_of=2026-11-04", headers=headers)
        assert second.status_code == 429, second.text
        assert second.json()["detail"]["code"] == "ai_demo_quota_reached"
        assert client.get("/api/v4/ai/status", headers=headers).status_code == 200

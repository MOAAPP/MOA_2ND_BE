"""Stage 7 regressions: time-cutoff isolation and canonical initial demo state.

These tests use the original 20-user JSON ZIP checked into the backend.
They do not substitute frontend fixtures, forecast-oracle rows or fake API output.
"""
from datetime import date

from fastapi.testclient import TestClient

from app.main import app
from app.services.dataset import load_dataset

client = TestClient(app)


def test_frontend_can_boot_without_user_or_date_selection():
    health = client.get("/api/v4/health")
    assert health.status_code == 200, health.text
    assert health.json()["users"] == 20
    demo = client.get("/api/v4/demo/default")
    assert demo.status_code == 200, demo.text
    uid, as_of = demo.json()["user_id"], demo.json()["as_of_date"]
    assert uid in client.get("/api/v4/users").json()["users"]
    assert as_of == "2026-11-04"
    home = client.get(f"/api/v4/users/{uid}/home", params={"as_of": as_of})
    assert home.status_code == 200, home.text
    assert home.json()["user_id"] == "S01"
    assert home.json()["available_cash"] == 27800
    assert len(home.json()["recent_transactions"]) > 0
    assert len(home.json()["daily_cashflow"]) == 14
    assert len(home.json()["upcoming_essential_payments"]) > 0


def test_all_20_account_balances_match_independent_snapshot():
    ds = load_dataset()
    assert len(ds.profiles) == 20
    for uid in sorted(ds.profiles):
        daily = next(x for x in ds.get_user(uid).daily if x["date"] == "2026-11-04")
        home = client.get(f"/api/v4/users/{uid}/home", params={"as_of": "2026-11-04"})
        assert home.status_code == 200, (uid, home.text)
        body = home.json()
        assert body["available_cash"] == daily["account_balance_eod"]
        assert body["user_id"] == uid
        assert all(t["user_id"] == uid for t in body["recent_transactions"])
        assert all(t["date"] > "2026-11-04" for t in body["daily_cashflow"])


def test_past_date_snapshot_excludes_future_known_investments_and_purchase_intents():
    uid, as_of = "S01", "2026-10-01"
    response = client.get(f"/api/v4/users/{uid}/dataset-snapshot", params={"as_of": as_of})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["snapshot_date"] == as_of
    for key in ("transactions", "daily", "balance_checks", "investments", "purchase_intents"):
        assert all(row["date"] <= as_of for row in data[key]), key
        assert all(row["user_id"] == uid for row in data[key]), key
        assert all(not row.get("future_scenario", False) for row in data[key]), key
    assert all(row.get("status") in ("confirmed_synthetic", "confirmed_user") for row in data["transactions"])


def test_future_cutoff_and_unknown_user_fail_instead_of_returning_other_data():
    assert client.get("/api/v4/users/S01/dataset-snapshot", params={"as_of": "2026-11-05"}).status_code == 422
    assert client.get("/api/v4/users/S01/dataset-snapshot", params={"as_of": "2026-09-10"}).status_code == 422
    assert client.get("/api/v4/users/UNKNOWN/dataset-snapshot").status_code == 404


def test_stage7_openapi_has_real_registered_handlers():
    paths = client.get("/openapi.json").json()["paths"]
    required = {
        "/api/v4/health", "/api/v4/users", "/api/v4/demo/default",
        "/api/v4/users/{user_id}/home", "/api/v4/users/{user_id}/dataset-snapshot",
        "/api/v4/users/{user_id}/forecast", "/api/v4/users/{user_id}/briefing",
        "/api/v4/users/{user_id}/spending-capacity", "/api/v4/scenarios/compare",
    }
    assert not (required - paths), required - paths
    assert client.get("/docs").status_code == 200

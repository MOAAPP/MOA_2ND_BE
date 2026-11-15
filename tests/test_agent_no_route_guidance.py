"""No-route guidance is grounded in the canonical no-purchase scenario."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def request_payload(*, horizon_days=30, minimum_balance=None, confirmations=None):
    conditions = {
        "user_id": "S06",
        "as_of": "2026-11-04",
        "purchase_amount": 1000,
        "purchase_required": False,
    }
    if minimum_balance is not None:
        conditions["minimum_balance"] = minimum_balance
    if confirmations:
        conditions["confirmations_required"] = confirmations
    return {
        "conditions": conditions,
        "horizon_days": horizon_days,
        "max_evaluations": 20,
    }


def test_unsafe_no_purchase_explains_baseline_shortfall_instead_of_unsafe_recommendation():
    response = client.post("/api/v4/agent/search", json=request_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "no_valid_route"
    assert body["routes"] == []
    assert body["valid_count"] == 0
    guidance = body["no_route_guidance"]
    assert guidance["reason"] == "baseline_cashflow_at_risk"
    base = guidance["baseline_no_purchase"]
    assert base["horizon_days"] == 30
    assert base["forecast_source"] == body["forecast_source"]
    assert base["minimum_balance"] < 0
    assert base["ending_balance"] < 0
    assert base["first_shortfall_date"] == "2026-11-22"
    assert base["minimum_balance_met"] is False
    assert base["essential_payments_covered"] is False
    assert "구매를 포기" in guidance["summary"]
    assert guidance["summary"] == body["conflict_explanations"][0]
    assert guidance["actions_are_validated"] is False
    assert len(guidance["next_steps"]) >= 2


def test_valid_short_horizon_does_not_get_no_route_guidance():
    response = client.post("/api/v4/agent/search", json=request_payload(horizon_days=14))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["routes"]
    assert body["no_route_guidance"] is None
    assert all(r["hard_constraints_met"] for r in body["routes"])


def test_unattainable_user_minimum_is_not_misreported_as_existing_overdraft():
    response = client.post("/api/v4/agent/search", json=request_payload(
        horizon_days=14, minimum_balance=100_000_000
    ))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "no_valid_route"
    guidance = body["no_route_guidance"]
    assert guidance["reason"] == "minimum_balance_unmet"
    base = guidance["baseline_no_purchase"]
    assert base["minimum_balance"] >= 0
    assert base["required_minimum_balance"] == 100_000_000
    assert guidance["actions_are_validated"] is False


def test_confirmation_state_is_unchanged_and_never_invents_guidance():
    response = client.post("/api/v4/agent/search", json=request_payload(
        confirmations=["저축 목표 금액 확인 필요"]
    ))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "needs_confirmation"
    assert body["no_route_guidance"] is None
    assert body["evaluated_count"] == 0

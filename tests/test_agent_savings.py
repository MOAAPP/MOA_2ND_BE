"""Goal-centered savings advice must never recommend an unverified payment plan."""
from datetime import date
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services.agent_savings import SavingsAdviceRequest, recommend_savings
from app.stage3_models import SavingsPlanRequest

DAY = date(2026, 11, 4)
client = TestClient(app)


def request(amount=70000, **overrides):
    return SavingsAdviceRequest(plan=SavingsPlanRequest(
        **{"user_id": "S01", "as_of": DAY, "goal_amount": 600000,
           "current_savings": 200000, "deposit_amount": amount,
           "duration_months": 6, **overrides},
    ))


def fake_result(r, safe=True):
    as_of = r.as_of or DAY
    start = r.start_date or date(2026, 11, 5)
    payments = 6 * r.deposit_amount
    if r.first_deposit_amount is not None:
        payments += r.first_deposit_amount - r.deposit_amount
    principal = r.current_savings + payments
    return SimpleNamespace(
        cashflow_validation_status="satisfies_assumptions" if safe else "fails_assumptions",
        goal_reached_by_maturity=principal >= r.goal_amount,
        projected_maturity_balance=principal,
        projected_goal_progress_percent=min(100.0, 100 * principal / r.goal_amount),
        remaining_goal_amount=max(0, r.goal_amount - principal),
        goal_reached_date=date(2027, 4, 5) if principal >= r.goal_amount else None,
        minimum_payment_account_balance=1000 if safe else -500,
        first_shortfall_date=None if safe else date(2026, 11, 5),
        cashflow_validated_through=date(2027, 2, 2),
        start_date=start,
    )


def test_chooses_checked_lower_payment_when_original_is_unsafe(monkeypatch):
    from app.services import agent_savings
    seen = []
    def emulate(ds, plan):
        seen.append(plan.deposit_amount)
        return fake_result(plan, safe=plan.deposit_amount <= 35000)
    monkeypatch.setattr(agent_savings, "plan_savings", emulate)
    result = recommend_savings(object(), request())
    assert result.status == "recommended"
    assert result.recommended_option_id == "half"
    chosen = next(o for o in result.alternatives if o.id == "half")
    assert chosen.deposit_amount == 35000
    assert chosen.payment_feasible_in_checked_window
    assert chosen.remaining_goal_amount == 190000
    assert not next(o for o in result.alternatives if o.id == "requested").payment_feasible_in_checked_window
    assert 0 in seen
    assert result.caveats


def test_no_savings_recommendation_when_baseline_account_is_unsafe(monkeypatch):
    from app.services import agent_savings
    monkeypatch.setattr(agent_savings, "plan_savings", lambda ds, plan: fake_result(plan, safe=False))
    result = recommend_savings(object(), request())
    assert result.status == "base_cashflow_at_risk"
    assert result.recommended_option_id is None
    assert all(not x.payment_feasible_in_checked_window for x in result.alternatives)


def test_no_feasible_deposit_when_only_baseline_passes(monkeypatch):
    from app.services import agent_savings
    monkeypatch.setattr(agent_savings, "plan_savings",
                        lambda ds, plan: fake_result(plan, safe=plan.deposit_amount == 0))
    result = recommend_savings(object(), request())
    assert result.status == "no_feasible_deposit"
    assert result.recommended_option_id is None


def test_first_deposit_scales_with_regular_amount_and_no_zero_suggestion(monkeypatch):
    from app.services import agent_savings
    seen = []
    def capture(ds, plan):
        seen.append((plan.deposit_amount, plan.first_deposit_amount))
        return fake_result(plan)
    monkeypatch.setattr(agent_savings, "plan_savings", capture)
    recommend_savings(object(), request(amount=3, first_deposit_amount=100))
    assert (0, 0) in seen
    assert (0, 0) in seen
    assert (1, 50) in seen
    assert (2, 75) in seen
    assert all(a != 0 or f == 0 for a, f in seen)
    assert len(seen) == 5  # requested, 75%, 50%, baseline, delayed


def test_api_returns_compact_explainable_results(monkeypatch):
    from app.api import agent
    monkeypatch.setattr(agent, "recommend_savings", lambda ds, payload: {
        "user_id": "S01", "as_of": DAY.isoformat(),
        "goal_amount": 600000, "status": "no_feasible_deposit",
        "recommended_option_id": None, "alternatives": [],
        "explanation": "안전한 납입액이 확인되지 않았어요.",
    })
    response = client.post("/api/v4/agent/savings/recommend", json={
        "plan": {"user_id": "S01", "as_of": DAY.isoformat(),
                 "goal_amount": 600000, "current_savings": 200000,
                 "deposit_amount": 70000, "duration_months": 6},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "no_feasible_deposit"
    assert body["recommended_option_id"] is None
    assert body["alternatives"] == []


def test_api_rejects_future_observations_and_malformed_plan():
    data = {"plan": {"user_id": "S01", "as_of": "2026-11-05",
                     "goal_amount": 600000, "deposit_amount": 70000}}
    assert client.post("/api/v4/agent/savings/recommend", json=data).status_code == 422
    data["plan"]["as_of"] = DAY.isoformat()
    data["plan"]["duration_months"] = 5
    assert client.post("/api/v4/agent/savings/recommend", json=data).status_code == 422

"""Savings schedules, interest estimates, and bounded canonical cashflow checks."""
from datetime import date

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services.dataset import load_dataset
from app.services.finance import FinanceProblem
from app.services.savings_plan import savings_projection, plan_savings
from app.stage3_models import SavingsPlanRequest

client = TestClient(app)
DAY = date(2026, 11, 4)


def plan_request(**changes):
    return SavingsPlanRequest(
        **{"user_id": "S01", "as_of": DAY, "goal_amount": 60000,
           "current_savings": 10000, "deposit_amount": 10000,
           "duration_months": 6, **changes}
    )


def test_monthly_deposits_are_anchored_and_goal_date_is_derived():
    req = SavingsPlanRequest(user_id="S01", goal_amount=30000,
                             deposit_amount=10000, duration_months=3,
                             start_date=date(2026, 1, 31))
    start, maturity, deposits, points, interest, reached, total = savings_projection(
        req, date(2026, 1, 30))
    assert start == date(2026, 1, 31)
    assert maturity == date(2026, 4, 30)
    assert [when for when, _ in deposits] == [date(2026, 1, 31),
        date(2026, 2, 28), date(2026, 3, 31)]
    assert [value for _, value in deposits] == [10000] * 3
    assert interest is None
    assert total == 30000
    assert reached == date(2026, 3, 31)
    assert points[-1].goal_progress_percent == 100


def test_weekly_schedule_first_payment_and_noncompound_interest():
    req = plan_request(
        first_deposit_amount=25000, deposit_frequency="weekly",
        duration_months=3, annual_rate_percent="12",
    )
    _, maturity, deposits, points, interest, reached, total = savings_projection(req, DAY)
    assert deposits[0] == (date(2026, 11, 5), 25000)
    assert deposits[1] == (date(2026, 11, 12), 10000)
    assert all((deposits[i+1][0] - deposits[i][0]).days == 7 for i in range(len(deposits)-1))
    assert all(when < maturity for when, _ in deposits)
    assert interest is not None and interest > 0
    assert all(point.interest_credited == 0 for point in points[:-1])
    assert points[-1].interest_credited == interest
    assert total == req.current_savings + sum(x[1] for x in deposits) + interest
    assert reached is not None


def test_without_interest_rate_no_returns_are_fabricated():
    req = plan_request(current_savings=0, deposit_amount=1000, goal_amount=100000,
                       duration_months=3)
    _, _, deposits, _, interest, reached, total = savings_projection(req, DAY)
    assert interest is None
    assert total == sum(value for _, value in deposits)
    assert reached is None


def test_start_date_cannot_be_on_observation_day():
    with pytest.raises(FinanceProblem, match="savings_start_date_must_follow_observation_date"):
        savings_projection(plan_request(start_date=DAY), DAY)


def test_api_returns_12_month_projection_with_90_day_cashflow_limit():
    response = client.post("/api/v4/savings/plan", json={
        "user_id": "S01", "as_of": DAY.isoformat(),
        "goal_amount": 150000, "current_savings": 10000,
        "deposit_amount": 10000, "duration_months": 12,
        "forecast_daily_spend_override": 0,
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["maturity_date"] == "2027-11-05"
    assert data["planned_deposit_count"] == 12
    assert data["planned_deposits_total"] == 120000
    assert data["projected_principal"] == 130000
    assert data["estimated_interest_before_tax"] is None
    assert data["remaining_goal_amount"] == 20000
    assert data["cashflow_validation_days"] == 90
    assert len(data["cashflow_preview"]) == 90
    assert data["deposits_within_validation"] == data["cashflow_preview"][-1]["savings_balance"]
    assert data["deposits_beyond_validation"] > 0
    assert "deposits_after_cashflow_preview_not_financially_validated" in data["warnings"]
    assert "maturity_balance_is_contribution_projection_not_long_term_cashflow_forecast" in data["warnings"]


def test_api_cashflow_uses_actual_plan_installments_only():
    ds = load_dataset()
    req = plan_request(duration_months=3, deposit_amount=2000,
                       forecast_daily_spend_override=0)
    result = plan_savings(ds, req)
    expected_deposits = sum(point.deposit_amount for point in result.timeline
                            if DAY < point.date <= result.cashflow_validated_through)
    assert result.deposits_within_validation == expected_deposits
    assert result.cashflow_preview[-1].savings_balance == expected_deposits
    assert result.projected_principal == req.current_savings + result.planned_deposits_total
    assert result.goal_reached_by_maturity is False


def test_api_rejects_invalid_dates_and_rate():
    base = {"user_id": "S01", "as_of": "2026-11-04",
            "goal_amount": 20000, "deposit_amount": 5000}
    assert client.post("/api/v4/savings/plan", json={**base,
        "start_date": "2026-11-04"}).status_code == 422
    assert client.post("/api/v4/savings/plan", json={**base,
        "as_of": "2026-11-05"}).status_code == 422
    assert client.post("/api/v4/savings/plan", json={**base,
        "annual_rate_percent": -3}).status_code == 422

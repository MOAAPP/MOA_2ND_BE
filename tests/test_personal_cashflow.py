"""User-mode inputs never read S01 or the ZIP's preloaded financial fixtures."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
TODAY = datetime.now(ZoneInfo("Asia/Seoul")).date()
D = TODAY.isoformat()


def snapshot(**kw):
    return {
        "as_of": D, "balance": 500000, "minimum_balance": 100000,
        "monthly_income": 2000000, "payday": 25,
        "monthly_variable_budget": 0,
        "bills": [], "planned_transactions": [],
        **kw,
    }


def compare(**kw):
    s = kw.pop("snapshot", snapshot())
    return client.post("/api/v4/personal/compare", json={
        "snapshot": s, "purchase_amount": 200000,
        "horizon_days": 30, **kw,
    })


def test_user_balances_not_canned_S01_and_not_written_to_dataset():
    r = compare()
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["source"] == "user_entered_hypothetical"
    assert o["inputs_not_saved"] is True
    assert o["available_cash"] == 500000
    assert o["as_of"] == D
    assert o["calculator"] == "python_finance_cashflow_rules"
    assert o["forecast_source"] == "no_variable_spend_assumption"
    ids = [p["scenario_id"] for p in o["paths"]]
    assert ids == ["purchase_now", "no_purchase", "purchase_delay",
                   "virtual_savings", "virtual_investment", "mixed"]
    assert len(o["paths"][0]["daily_cashflow"]) == 30
    assert all(p["available_cash"] == 500000 for p in o["paths"])
    bought, kept = o["paths"][:2]
    assert kept["ending_balance"] - bought["ending_balance"] == 200000
    assert bought["daily_cashflow"][0]["lowest_balance"] == 300000
    assert "variable_future_spending_not_provided_zero_assumed" in o["warnings"]


def test_monthly_bill_can_cross_cash_buffer_even_before_salary():
    due = (TODAY + timedelta(days=1)).day
    s = snapshot(
        balance=240000, minimum_balance=100000, monthly_income=0,
        bills=[{"day": due, "amount": 100000, "kind": "rent"}],
    )
    o = compare(snapshot=s, purchase_amount=70000, horizon_days=7).json()
    buy, keep = o["paths"][:2]
    assert buy["minimum_balance"] == 70000
    assert buy["first_shortfall_date"] == (TODAY + timedelta(days=1)).isoformat()
    assert buy["minimum_balance_met"] is False
    # Essential rent is charged first and covered; hypothetical purchase still breaks the buffer.
    assert buy["essential_payments_covered"] is True
    assert keep["minimum_balance_met"] is True
    assert keep["essential_payments_covered"] is True


def test_past_transactions_cannot_be_double_subtracted_from_anchor():
    past = (TODAY - timedelta(days=1)).isoformat()
    r = compare(snapshot=snapshot(planned_transactions=[{
        "date": past, "kind": "expense", "amount": 120000,
        "payment_method": "debit",
    }]))
    assert r.status_code == 422
    assert "planned_transaction_must_be_after_balance_snapshot" in r.text


def test_credit_and_cash_authorization_not_immediately_debited_or_rebilled():
    future = (TODAY + timedelta(days=2)).isoformat()
    s = snapshot(balance=1000000, monthly_income=0,
        planned_transactions=[
            {"date": future, "kind": "expense", "amount": 80000,
             "payment_method": "credit"},
            {"date": future, "kind": "expense", "amount": 40000,
             "payment_method": "cash"},
        ])
    r = compare(snapshot=s, horizon_days=7).json()
    assert r["paths"][1]["ending_balance"] == 1000000
    assert "credit_or_cash_transactions_without_confirmed_bank_debit_excluded" in r["warnings"]


def test_user_budget_is_explicit_rule_assumption_not_fabricated_ML():
    s = snapshot(balance=500000, monthly_income=0,
                 monthly_variable_budget=300000)
    o = compare(snapshot=s, horizon_days=7).json()
    assert o["paths"][1]["ending_balance"] == 430000
    assert o["forecast_source"] == "user_budget_assumption_not_ml"


def test_month_end_clamped_payday_and_bills():
    # Use the next February from current year, at a valid previous as_of
    # only if it exists; test the pure calendar rule independently.
    from app.services.personal_cashflow import _clamp_month_day
    assert _clamp_month_day(date(2026, 2, 28), 31)
    assert not _clamp_month_day(date(2026, 2, 27), 31)
    assert _clamp_month_day(date(2028, 2, 29), 31)


def test_goal_rules_are_not_misrepresented_as_AI_or_investment_advice():
    s = snapshot(balance=300000, monthly_income=0)
    r = client.post("/api/v4/personal/goal-analyze", json={
        "snapshot": s, "target_amount": 250000,
        "horizon_days": 7, "goal_type": "purchase",
    })
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["source"] == "personal_rules_not_llm"
    assert result["evaluated_count"] == 2
    assert result["valid_count"] == 0
    assert "minimum_balance" in result["rejection_counts"]
    assert result["inputs_not_saved"] is True
    blocked = client.post("/api/v4/personal/goal-analyze", json={
        "snapshot": s, "target_amount": 100000,
        "horizon_days": 30, "goal_type": "investment",
        "investment_allowed": False,
    })
    assert blocked.status_code == 200
    assert blocked.json()["rejection_counts"] == {"investment_permission": 1}


def test_reject_personal_identifiers_and_bad_numbers_and_future_dates():
    assert compare(snapshot=snapshot(name="홍길동")).status_code == 422
    assert compare(snapshot=snapshot(account_number="1234")).status_code == 422
    assert compare(snapshot=snapshot(balance=-1)).status_code == 422
    assert compare(snapshot=snapshot(as_of=(TODAY + timedelta(days=1)).isoformat())).status_code == 422
    assert compare(horizon_days=91).status_code == 422
    assert compare(payment_method="credit").status_code == 422


def test_production_personal_input_fails_closed_without_secure_gateway(monkeypatch):
    monkeypatch.setenv("MOA_DEPLOYMENT_MODE", "production")
    monkeypatch.delenv("MOA_REQUIRE_GATEWAY", raising=False)
    monkeypatch.delenv("MOA_GATEWAY_SECRET", raising=False)
    r = compare()
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "personal_preview_requires_secure_gateway"
    monkeypatch.delenv("MOA_DEPLOYMENT_MODE", raising=False)

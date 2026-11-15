"""Integrated savings + purchase + multi-asset Agent regression/contract tests."""
from datetime import date

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
DAY = "2026-11-04"


def goal_payload(uid="S06", **changes):
    base = {
        "user_id": uid, "as_of": DAY, "horizon_days": 14,
        "savings_plan": {
            "user_id": uid, "as_of": DAY,
            "goal_amount": 40000, "current_savings": 10000,
            "deposit_amount": 5000, "duration_months": 6,
            "forecast_daily_spend_override": 0,
        },
        "purchase_amount": 1000,
        "purchase_required": False,
        "purchase_delay_days": 3,
        "investment_trades": [
            {"trade_id": "etf-buy", "code": "ETF-A", "asset_type": "etf",
             "side": "buy", "trade_date": "2026-11-05",
             "quantity": 2, "unit_price": 1000},
            {"trade_id": "stock-buy", "code": "STOCK-B", "asset_type": "stock",
             "side": "buy", "trade_date": "2026-11-06",
             "quantity": 1, "unit_price": 500},
            {"trade_id": "etf-sell", "code": "ETF-A", "asset_type": "etf",
             "side": "sell", "trade_date": "2026-11-09",
             "settlement_date": "2026-11-10",
             "quantity": 1, "unit_price": 1200},
        ],
        "investment_price_marks": [
            {"code": "ETF-A", "date": "2026-11-11", "unit_price": 900},
            {"code": "STOCK-B", "date": "2026-11-11", "unit_price": 400},
        ],
    }
    return {**base, **changes}


def test_goal_agent_is_available_as_opt_in_fastapi_endpoint():
    schema = client.get("/openapi.json").json()
    assert "/api/v4/agent/goal-plan" in schema["paths"]
    req = schema["components"]["schemas"]["IntegratedGoalRequest"]
    assert req["examples"]


def test_all_paths_use_one_reconciled_cashflow_and_chart():
    response = client.post("/api/v4/agent/goal-plan", json=goal_payload())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["user_id"] == "S06"
    assert result["status"] == "ok", result
    assert result["routes"], result
    assert result["evaluated_count"] > 0
    assert result["valid_count"] >= len(result["routes"])
    assert result["evaluated_count"] <= 36
    assert result["baseline_minimum_payment_account_balance"] is not None
    for route in result["routes"]:
        assert route["all_hard_constraints_met"]
        assert all(route["checks"].values())
        assert len(route["chart"]) == 14
        assert route["chart"][-1]["payment_account_balance"] == route["payment_account_ending_balance"]
        assert route["chart"][-1]["total_virtual_assets"] == (
            route["chart"][-1]["savings_principal_balance"] +
            route["chart"][-1]["portfolio_market_value"]
        )
        new_savings = route["chart"][-1]["savings_principal_balance"] - 10000
        cash_investment = (-2500 + 1200) if route["investment_included"] else 0
        assert route["chart"][-1]["balance_delta_vs_no_actions"] == (
            -route["purchase_amount"] - new_savings + cash_investment
        )
        if route["investment_included"]:
            assert route["investment_buy_amount"] == 2500
            assert route["investment_sell_proceeds"] == 1200
            assert route["investment_market_value"] == 1300
            assert route["investment_total_profit_loss"] == 0
    if result["status"] == "ok":
        assert result["recommended_route_id"] == result["routes"][0]["id"]
    else:
        assert result["recommended_route_id"] is None
        assert not result["routes"]


def test_investment_loss_does_not_withdraw_any_extra_cash():
    a = goal_payload(investment_required=True)
    b = goal_payload(investment_required=True, valuation_loss_percent=100)
    one = client.post("/api/v4/agent/goal-plan", json=a)
    two = client.post("/api/v4/agent/goal-plan", json=b)
    assert one.status_code == two.status_code == 200
    left, right = one.json(), two.json()
    assert left["requested_plan_checks"] == right["requested_plan_checks"]
    left_routes = {(x["purchase_strategy"], x["savings_strategy"]): x for x in left["routes"]}
    right_routes = {(x["purchase_strategy"], x["savings_strategy"]): x for x in right["routes"]}
    for key in left_routes.keys() & right_routes.keys():
        l, r = left_routes[key], right_routes[key]
        assert l["investment_included"] and r["investment_included"]
        assert [x["payment_account_balance"] for x in l["chart"]] == [
            x["payment_account_balance"] for x in r["chart"]]
        assert r["investment_market_value"] <= l["investment_market_value"]


def test_required_purchase_and_investment_never_silently_removed():
    request = goal_payload(purchase_required=True, investment_required=True)
    response = client.post("/api/v4/agent/goal-plan", json=request)
    assert response.status_code == 200, response.text
    for route in response.json()["routes"]:
        assert route["purchase_amount"] == request["purchase_amount"]
        assert route["purchase_strategy"] != "no_purchase"
        assert route["investment_included"] is True


def test_mandatory_unreachable_goal_never_claimed_satisfied():
    req = goal_payload(
        purchase_amount=0, investment_trades=[], investment_price_marks=[],
        savings_goal_required_by_maturity=True,
        savings_plan={**goal_payload()["savings_plan"], "goal_amount": 10_000_000},
    )
    response = client.post("/api/v4/agent/goal-plan", json=req)
    assert response.status_code == 200, response.text
    assert response.json()["status"] != "ok"
    assert response.json()["routes"] == []
    assert response.json()["recommended_route_id"] is None
    assert response.json()["requested_plan_checks"]["required_savings_goal"] is False


def test_base_cashflow_risk_prevents_false_recommendations():
    response = client.post("/api/v4/agent/goal-plan", json=goal_payload("S01"))
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["baseline_minimum_payment_account_balance"] < 0
            or not result["baseline_essential_payments_covered"])
    assert result["status"] == "base_cashflow_at_risk"
    assert result["routes"] == []
    assert result["recommended_route_id"] is None
    assert result["conflict_explanations"]


def test_validation_rejects_cross_user_and_bad_observation_dates():
    p = goal_payload()
    p["savings_plan"]["user_id"] = "S01"
    assert client.post("/api/v4/agent/goal-plan", json=p).status_code == 422
    p = goal_payload(as_of="2026-11-03")
    assert client.post("/api/v4/agent/goal-plan", json=p).status_code == 422
    p = goal_payload(as_of="2026-11-05")
    p["savings_plan"]["as_of"] = "2026-11-05"
    assert client.post("/api/v4/agent/goal-plan", json=p).status_code == 422
    p = goal_payload("S99")
    # S99 matches the pattern, but does not exist in the dataset.
    p["savings_plan"]["user_id"] = "S99"
    assert client.post("/api/v4/agent/goal-plan", json=p).status_code == 404


def test_invalid_investment_and_unmodeled_stress_fail_explicitly():
    p = goal_payload()
    p["investment_trades"][-1]["quantity"] = 4
    r = client.post("/api/v4/agent/goal-plan", json=p)
    assert r.status_code == 422
    assert "portfolio_sell_exceeds_hypothetical_holdings" in r.text
    p = goal_payload()
    p["savings_plan"]["stress"] = "higher_spending"
    r = client.post("/api/v4/agent/goal-plan", json=p)
    assert r.status_code == 422
    assert "integrated_goal_only_baseline_stress_supported" in r.text


def test_existing_parallel_savings_and_portfolio_endpoints_unchanged():
    parallel = client.post("/api/v4/scenarios/compare", json={
        "user_id": "S01", "as_of": DAY, "purchase_amount": 1000})
    savings = client.post("/api/v4/savings/plan", json={
        "user_id": "S01", "as_of": DAY, "goal_amount": 20000,
        "deposit_amount": 2000, "duration_months": 3})
    investment = client.post("/api/v4/portfolio/simulate", json={
        "user_id": "S01", "as_of": DAY, "horizon_days": 14,
        "forecast_daily_spend_override": 0,
        "trades": [{
            "trade_id": "buy", "code": "AAA", "asset_type": "stock",
            "side": "buy", "trade_date": "2026-11-05",
            "quantity": 1, "unit_price": 1000,
        }]
    })
    assert parallel.status_code == 200, parallel.text
    assert savings.status_code == 200, savings.text
    assert investment.status_code == 200, investment.text
    assert len(parallel.json()["paths"]) == 6
    assert len(savings.json()["timeline"]) > 0
    assert len(investment.json()["daily_chart"]) == 14


def test_one_month_delayed_savings_not_called_checked_with_14_day_horizon():
    request = goal_payload(
        purchase_amount=0, investment_trades=[], investment_price_marks=[],
    )
    response = client.post("/api/v4/agent/goal-plan", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "ok"
    # Delaying a first payment beyond the 14-day preview is not a verified
    # savings deposit, even though the zero-activity cashflow may be healthy.
    assert all(route["savings_strategy"] != "delay_one_month" for route in result["routes"])
    assert all(route["checks"]["savings_deposit_validated"] for route in result["routes"])


def test_required_goal_inputs_are_explicit_and_no_investment_is_fabricated():
    bad_purchase = goal_payload(purchase_required=True, purchase_amount=0)
    assert client.post("/api/v4/agent/goal-plan", json=bad_purchase).status_code == 422
    bad_investment = goal_payload(
        investment_required=True, investment_trades=[], investment_price_marks=[])
    assert client.post("/api/v4/agent/goal-plan", json=bad_investment).status_code == 422

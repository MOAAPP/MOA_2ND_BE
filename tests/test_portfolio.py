"""Contract tests for hypothetical multi-asset trade accounting and cashflow reconciliation."""
from datetime import date

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
DAY = date(2026, 11, 4)


def payload(**changes):
    base = {
        "user_id": "S01",
        "as_of": "2026-11-04",
        "horizon_days": 14,
        "forecast_daily_spend_override": 0,
        "trades": [
            {"trade_id": "buy-a", "code": "ETF-A", "asset_type": "etf",
             "side": "buy", "trade_date": "2026-11-05",
             "quantity": 2, "unit_price": 10000},
            {"trade_id": "buy-b", "code": "STOCK-B", "asset_type": "stock",
             "side": "buy", "trade_date": "2026-11-06",
             "quantity": 3, "unit_price": 20000},
            {"trade_id": "sell-a", "code": "ETF-A", "asset_type": "etf",
             "side": "sell", "trade_date": "2026-11-09",
             "settlement_date": "2026-11-10",
             "quantity": 1, "unit_price": 12000},
        ],
        "price_marks": [
            {"code": "ETF-A", "date": "2026-11-12", "unit_price": 9000},
            {"code": "STOCK-B", "date": "2026-11-12", "unit_price": 15000},
        ],
    }
    return {**base, **changes}


def run(**changes):
    response = client.post("/api/v4/portfolio/simulate", json=payload(**changes))
    assert response.status_code == 200, response.text
    return response.json()


def test_two_instruments_partial_sale_realized_unrealized_and_cash_reconcile():
    data = run()
    positions = {p["code"]: p for p in data["positions"]}
    assert len(positions) == 2
    assert positions["ETF-A"]["asset_type"] == "etf"
    assert positions["ETF-A"]["quantity"] == 1
    assert positions["ETF-A"]["cost_basis"] == 10000
    assert positions["ETF-A"]["realized_profit_loss"] == 2000
    assert positions["ETF-A"]["unrealized_profit_loss"] == -1000
    assert positions["STOCK-B"]["quantity"] == 3
    assert positions["STOCK-B"]["unrealized_profit_loss"] == -15000
    assert data["total_buy_amount"] == 80000
    assert data["total_sell_proceeds"] == 12000
    assert data["settled_sell_proceeds"] == 12000
    assert data["net_payment_account_cash_impact"] == -68000
    assert data["market_value"] == 54000
    assert data["remaining_cost_basis"] == 70000
    assert data["realized_profit_loss"] == 2000
    assert data["unrealized_profit_loss"] == -16000
    assert data["total_profit_loss"] == -14000
    assert len(data["daily_chart"]) == 14
    assert data["daily_chart"][-1]["investment_market_value"] == 54000
    assert data["daily_chart"][-1]["total_profit_loss"] == -14000
    assert data["daily_chart"][-1]["balance_delta_vs_no_trades"] == -68000
    assert data["forecast_source"] == "explicit_daily_spend_assumption_not_ml"
    assert data["source"] == "hypothetical_synthetic_dataset"


def test_cash_influenced_once_even_if_valuation_drops_to_zero():
    base = run()
    loss = run(valuation_loss_percent=100)
    assert loss["market_value"] == 0
    assert loss["total_profit_loss"] == -68000
    assert loss["net_payment_account_cash_impact"] == base["net_payment_account_cash_impact"]
    assert [d["payment_account_balance"] for d in loss["daily_chart"]] == [
        d["payment_account_balance"] for d in base["daily_chart"]]
    assert "additional_portfolio_valuation_loss_is_hypothetical_only" in loss["warnings"]


def test_unsettled_sale_does_not_create_cash_in_validation_window():
    p = payload()
    p["trades"][-1]["settlement_date"] = "2026-12-20"
    response = client.post("/api/v4/portfolio/simulate", json=p)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["realized_profit_loss"] == 2000
    assert data["settled_sell_proceeds"] == 0
    assert data["unsettled_sell_proceeds"] == 12000
    assert data["net_payment_account_cash_impact"] == -80000
    assert data["daily_chart"][-1]["balance_delta_vs_no_trades"] == -80000
    assert data["trades"][-1]["cash_in_validation_window"] is False
    assert "some_sale_proceeds_settle_after_the_cashflow_window" in data["warnings"]


def test_no_mark_does_not_invent_market_return():
    # One buy: the explicitly entered transaction price is carried forward,
    # and no market gain is synthesized.
    data = run(trades=[payload()["trades"][0]], price_marks=[])
    assert data["total_profit_loss"] == 0
    assert data["market_value"] == data["total_buy_amount"] == 20000
    assert data["positions"][0]["valuation_price_source"] == "user_trade_price_carried_forward"
    assert data["realized_profit_loss"] == 0


def test_same_day_sale_without_settlement_is_explained():
    trades = payload()["trades"]
    trades[-1].pop("settlement_date")
    data = run(trades=trades)
    assert data["settled_sell_proceeds"] == 12000
    assert "sale_settlement_defaults_to_same_day_sandbox_assumption" in data["warnings"]


def test_cannot_sell_existing_dataset_virtual_holdings_without_hypothetical_buy():
    response = client.post("/api/v4/portfolio/simulate", json=payload(
        trades=[payload()["trades"][-1]], price_marks=[]))
    assert response.status_code == 422
    assert "portfolio_sell_exceeds_hypothetical_holdings" in response.text


def test_oversell_and_trade_before_observation_rejected():
    trades = payload()["trades"]
    trades[-1]["quantity"] = 3
    assert client.post("/api/v4/portfolio/simulate", json=payload(trades=trades)).status_code == 422
    trades = payload()["trades"]
    trades[0]["trade_date"] = DAY.isoformat()
    assert client.post("/api/v4/portfolio/simulate", json=payload(trades=trades)).status_code == 422


def test_nonexistent_instrument_mark_and_duplicate_marks_rejected():
    bad = [{"code": "OUTSIDE", "date": "2026-11-12", "unit_price": 50}]
    assert client.post("/api/v4/portfolio/simulate", json=payload(price_marks=bad)).status_code == 422
    marks = payload()["price_marks"]
    assert client.post("/api/v4/portfolio/simulate", json=payload(
        price_marks=marks + [marks[0]])).status_code == 422


def test_future_day_and_cross_user_data_are_rejected():
    assert client.post("/api/v4/portfolio/simulate", json=payload(
        as_of="2026-11-05")).status_code == 422
    assert client.post("/api/v4/portfolio/simulate", json=payload(
        user_id="S99")).status_code == 404
    assert client.post("/api/v4/portfolio/simulate", json=payload(
        user_id="Z99")).status_code == 422


def test_invalid_settlement_or_asset_type_is_rejected():
    trades = payload()["trades"]
    trades[-1]["settlement_date"] = "2026-11-08"
    assert client.post("/api/v4/portfolio/simulate", json=payload(trades=trades)).status_code == 422
    trades = payload()["trades"]
    trades[1]["code"] = "ETF-A"
    assert client.post("/api/v4/portfolio/simulate", json=payload(trades=trades)).status_code == 422


def test_six_world_api_remains_unchanged_for_existing_frontend():
    r = client.post("/api/v4/scenarios/compare",
                    json={"user_id": "S01", "purchase_amount": 10000})
    assert r.status_code == 200, r.text
    assert len(r.json()["paths"]) == 6
    assert [x["scenario_id"] for x in r.json()["paths"]] == [
        "purchase_now", "no_purchase", "purchase_delay",
        "virtual_savings", "virtual_investment", "mixed",
    ]


def test_schema_documents_portfolio_request_and_keeps_new_api_opt_in():
    schema = client.get("/openapi.json").json()
    assert "/api/v4/portfolio/simulate" in schema["paths"]
    request_schema = schema["components"]["schemas"]["PortfolioSimulationRequest"]
    assert request_schema["examples"]
    example = request_schema["examples"][0]
    result = client.post("/api/v4/portfolio/simulate", json=example)
    assert result.status_code == 200, result.text


def test_repeat_buys_use_weighted_average_cost_and_track_closed_basis():
    trades = [
        {"trade_id": "buy-one", "code": "AAA", "asset_type": "stock",
         "side": "buy", "trade_date": "2026-11-05", "quantity": 1, "unit_price": 10000},
        {"trade_id": "buy-two", "code": "AAA", "asset_type": "stock",
         "side": "buy", "trade_date": "2026-11-06", "quantity": 1, "unit_price": 20000},
        {"trade_id": "sell-one", "code": "AAA", "asset_type": "stock",
         "side": "sell", "trade_date": "2026-11-07", "quantity": 1,
         "unit_price": 18000, "settlement_date": "2026-11-09"},
    ]
    data = run(trades=trades, price_marks=[
        {"code": "AAA", "date": "2026-11-08", "unit_price": 15000},
    ])
    item = data["positions"][0]
    assert item["quantity"] == 1
    assert item["average_purchase_price"] == 15000
    assert item["cost_basis"] == 15000
    assert item["realized_profit_loss"] == 3000
    assert item["unrealized_profit_loss"] == 0
    assert data["total_profit_loss"] == 3000
    assert data["net_payment_account_cash_impact"] == -12000


def test_mark_before_first_trade_is_rejected_not_silently_discarded():
    trades = payload()["trades"]
    trades[0]["trade_date"] = "2026-11-09"
    mark = [{"code": "ETF-A", "date": "2026-11-07", "unit_price": 500}]
    response = client.post("/api/v4/portfolio/simulate", json=payload(
        trades=trades[:1], price_marks=mark))
    assert response.status_code == 422
    assert "portfolio_price_mark_before_first_hypothetical_trade" in response.text


def test_payment_account_shortfall_is_not_described_as_safe():
    result = run()
    assert result["minimum_required_balance_met"] is False
    assert result["cashflow_validation_status"] == "fails_assumptions"
    assert "payment_account_or_essential_bills_at_risk_with_simulated_trades" in result["warnings"]

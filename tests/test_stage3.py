"""Stage 3 integration tests run against the exact SHA-pinned 20-user JSON ZIP."""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.core.settings import DATASET_PATH
from app.services.dataset import load_dataset
from app.services.stage3 import compare_worlds
from app.stage3_models import WorldCompareRequest

client = TestClient(app)
BASE = {"user_id": "S01", "purchase_amount": 10000, "purchase_delay_days": 7,
        "allocation": {"purchase": 4000, "savings": 3000, "investment": 2000, "cash": 1000}}


def get_result(**changes):
    data = {**BASE, **changes}
    response = client.post("/api/v4/scenarios/compare", json=data)
    assert response.status_code == 200, response.text
    return response.json()


def paths(result):
    return {p["scenario_id"]: p for p in result["paths"]}


def test_dataset_20_profiles_and_main_user():
    ds = load_dataset()
    assert len(ds.profiles) == 20
    assert set(ds.profiles) == {f"S{i:02}" for i in range(1, 21)}
    main = client.get("/api/v4/demo/default")
    assert main.status_code == 200
    assert main.json()["user_id"] == "S01"
    assert main.json()["user_count"] == 20
    assert main.json()["as_of_date"] == "2026-11-04"


def test_20_independent_user_homes_and_snapshots():
    ds = load_dataset()
    for user_id in sorted(ds.profiles):
        resp = client.get(f"/api/v4/users/{user_id}/home?as_of=2026-11-04")
        assert resp.status_code == 200, (user_id, resp.text)
        home = resp.json()
        assert home["user_id"] == user_id
        assert home["current_account_id"] == f"{user_id}-PAY"
        daily = next(x for x in ds.get_user(user_id).daily if x["date"] == "2026-11-04")
        assert home["available_cash"] == daily["account_balance_eod"]
        assert all(t["user_id"] == user_id for t in home["recent_transactions"])
        assert all(d["date"] > "2026-11-04" for d in home["daily_cashflow"])


def test_all_six_worlds_derived_from_same_snapshot():
    result = get_result()
    p = paths(result)
    assert len(p) == 6
    assert all(x["available_cash"] == result["available_cash"] for x in p.values())
    assert all(x["status"] == "calculated" for x in p.values())
    assert p["purchase_now"]["future_balances"]["14"] == p["no_purchase"]["future_balances"]["14"] - 10000
    assert p["purchase_delay"]["daily_cashflow"][2]["closing_balance"] == p["no_purchase"]["daily_cashflow"][2]["closing_balance"]
    assert p["virtual_savings"]["virtual_savings"] == 10000
    assert p["virtual_savings"]["future_balances"]["14"] == p["no_purchase"]["future_balances"]["14"] - 10000


def test_exact_20_person_no_cross_user_and_stable_comparison():
    a = get_result()
    b = get_result()
    assert a == b
    assert a["available_cash"] == 27800
    c = get_result(user_id="S02", allocation={"purchase": 4000, "savings": 3000, "investment": 2000, "cash": 1000})
    assert c["user_id"] == "S02"
    assert c["available_cash"] != a["available_cash"]
    assert all(e["user_id"] == "S02" for p in c["paths"] for e in p["events"])


def test_synthetic_investment_deducts_cash_once_and_separates_value():
    result = get_result(investment_amount=50000)
    p = paths(result)
    buy = p["virtual_investment"]
    quantity = buy["investment_quantity"]
    price = result["reference_investment_price"]
    assert price and quantity > 0
    assert buy["virtual_investment_value"] == quantity * price
    assert buy["future_balances"]["14"] == p["no_purchase"]["future_balances"]["14"] - quantity * price
    assert buy["virtual_investment_profit_loss"] == 0


def test_mixed_allocation_and_over_budget_rejected():
    result = get_result()
    p = paths(result)
    assert p["mixed"]["virtual_savings"] == 3000
    assert p["mixed"]["future_balances"]["14"] == p["no_purchase"]["future_balances"]["14"] - 7000
    over = client.post("/api/v4/scenarios/compare", json={**BASE, "allocation":
                       {"purchase": 30000, "savings": 2000, "investment": 0, "cash": 0}})
    assert over.status_code == 422


def test_savings_and_investment_without_purchase_are_both_applied_to_mixed():
    result = get_result(
        purchase_amount=9000,
        allocation={"purchase": 0, "savings": 3000, "investment": 5000, "cash": 1000},
        investment_unit_price=1000, investment_return_percent=-20,
        stress="investment_loss",
    )
    p = paths(result)
    mixed = p["mixed"]
    assert mixed["status"] == "calculated"
    assert mixed["purchase_completed"] is False
    assert mixed["virtual_savings"] == 3000
    assert mixed["investment_quantity"] == 5
    assert mixed["virtual_investment_value"] == 4000
    assert mixed["virtual_investment_profit_loss"] == -1000
    assert {e["event_type"] for e in mixed["events"]} == {
        "savings_deposit", "virtual_investment_buy",
    }
    # An asset loss does not create another payment-account withdrawal.
    assert mixed["future_balances"]["14"] == p["no_purchase"]["future_balances"]["14"] - 8000
    assert mixed["daily_cashflow"][-1]["savings_balance"] == 3000
    assert mixed["daily_cashflow"][-1]["investment_value"] == 4000


def test_credit_purchase_without_settlement_date_not_faked():
    p = paths(get_result(payment_method="credit"))
    assert p["purchase_now"]["status"] == "needs_confirmation"
    assert p["purchase_delay"]["status"] == "needs_confirmation"
    assert p["no_purchase"]["status"] == "calculated"


def test_income_delay_changes_eod_cashflow():
    a = paths(get_result())["no_purchase"]["future_balances"]["7"]
    b = paths(get_result(stress="delayed_income"))["no_purchase"]["future_balances"]["7"]
    assert a != b


def test_date_history_no_future_leakage():
    home = client.get("/api/v4/users/S01/home?as_of=2026-11-03").json()
    assert home["as_of_date"] == "2026-11-03"
    assert all(t["date"] <= "2026-11-03" for t in home["recent_transactions"])
    assert all(t["date"] <= "2026-11-03" for t in client.get(
        "/api/v4/users/S01/transactions?as_of=2026-11-03").json()["transactions"])
    assert client.get("/api/v4/users/S01/home?as_of=2026-11-05").status_code == 422
    assert client.get("/api/v4/users/Z99/home").status_code == 404


def test_savings_goal_user_defined_and_no_fabricated_dataset_goal():
    home = client.get("/api/v4/users/S01/home").json()
    assert home["savings_goals"] == []
    assert home["savings_goal_status"] == "not_in_canonical_dataset"
    p = paths(get_result(savings_goal_target=10000))
    assert p["virtual_savings"]["savings_goal_reached_date"] == "2026-11-05"


def test_save_does_not_modify_canonical_dataset(tmp_path, monkeypatch):
    import app.api.stage3 as handler
    monkeypatch.setattr(handler, "STORAGE_DIR", tmp_path)
    before = sha256(DATASET_PATH.read_bytes()).hexdigest()
    r = client.post("/api/v4/scenarios/save", json={"request": BASE, "label": "원본 보호 테스트"})
    assert r.status_code == 200, r.text
    saved = client.get("/api/v4/scenarios/" + r.json()["id"])
    assert saved.status_code == 200
    assert saved.json()["user_id"] == "S01"
    assert sha256(DATASET_PATH.read_bytes()).hexdigest() == before


def test_ml_interface_no_oracle_future_labels():
    response = client.get("/api/v4/ml/forecast-context?user_id=S01&as_of=2026-11-04")
    assert response.status_code == 200
    data = response.json()
    assert data["is_trained_ml_output"] is False
    assert all(d["date"] <= "2026-11-04" for d in data["recent_observed_variable_expenses"])


def test_virtual_etf_sale_settlement_realized_gain_and_remaining_holdings():
    original = get_result(investment_amount=100000)
    price = original["reference_investment_price"]
    assert price is not None
    sell = get_result(investment_amount=100000, virtual_sell_qty=1,
                      virtual_sell_unit_price=price+1000,
                      virtual_sell_date="2026-11-07")
    baseline = paths(sell)["no_purchase"]
    investment = paths(sell)["virtual_investment"]
    assert investment["status"] == "calculated"
    assert investment["investment_quantity"] == 1
    assert investment["virtual_investment_realized_profit_loss"] == 1000
    assert investment["virtual_investment_profit_loss"] == 1000
    assert investment["future_balances"]["14"] == baseline["future_balances"]["14"] - price + 1000
    assert any(e["event_type"] == "virtual_investment_sell" for e in investment["events"])


def test_delayed_credit_settlement_requires_separate_confirmed_debit_date():
    confirmation = get_result(
        payment_method="credit",
        credit_debit_date="2026-11-12",
        delayed_credit_debit_date="2026-11-15")
    p=paths(confirmation)
    assert p["purchase_now"]["status"] == "calculated"
    assert p["purchase_delay"]["status"] == "calculated"
    assert any(e["debit_at"].startswith("2026-11-15") for e in p["purchase_delay"]["events"])
    assert any(e["debit_at"].startswith("2026-11-12") for e in p["purchase_now"]["events"])


def test_selling_without_valid_holdings_is_rejected():
    bad = client.post("/api/v4/scenarios/compare", json={**BASE,
        "investment_amount": 1000, "virtual_sell_qty": 1, "virtual_sell_unit_price": 25000})
    assert bad.status_code == 422

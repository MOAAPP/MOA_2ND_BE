"""Step 2: savings/investment choices change future cash flow in a way driven by the request inputs."""
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
BASE = {"user_id": "S18", "as_of": "2026-11-04", "horizon_days": 90, "purchase_amount": 300000}


def run(**kw):
    r = client.post("/api/v4/scenarios/compare", json={**BASE, **kw})
    assert r.status_code == 200, r.text
    return {p["scenario_id"]: p for p in r.json()["paths"]}


def test_monthly_only_savings_produces_recurring_deposits():
    p = run(savings_amount=0, savings_monthly_amount=50000)["virtual_savings"]
    deposits = [e for e in p["events"] if e["event_type"] == "savings_deposit"]
    assert len(deposits) >= 3 and all(e["amount"] == 50000 for e in deposits)
    assert p["virtual_savings"] == 50000 * len(deposits)


def test_savings_amount_changes_balance_and_cash():
    a, b = run(savings_amount=100000)["virtual_savings"], run(savings_amount=200000)["virtual_savings"]
    assert b["virtual_savings"] - a["virtual_savings"] == 100000
    assert a["future_balances"]["14"] - b["future_balances"]["14"] == 100000


def test_interest_accrues_only_when_rate_given_and_scales_with_rate():
    none = run(savings_amount=1000000)["virtual_savings"]
    low = run(savings_amount=1000000, savings_annual_rate=0.02)["virtual_savings"]
    high = run(savings_amount=1000000, savings_annual_rate=0.04)["virtual_savings"]
    assert none["virtual_savings_interest"] == 0
    assert 0 < low["virtual_savings_interest"] < high["virtual_savings_interest"]
    assert abs(high["virtual_savings_interest"] - round(1000000 * 0.04 * 88 / 365)) <= 400
    # interest stays in savings; payment-account cash is unchanged by the rate
    assert low["future_balances"] == none["future_balances"]


def test_linear_return_ramps_while_immediate_jumps():
    imm = run(investment_amount=300000, investment_return_percent=10)["virtual_investment"]
    lin = run(investment_amount=300000, investment_return_percent=10, investment_return_mode="linear")["virtual_investment"]
    first = lambda p: [d for d in p["daily_cashflow"] if d["investment_value"]][0]
    assert first(lin)["investment_profit_loss"] < first(imm)["investment_profit_loss"]
    pl = [d["investment_profit_loss"] for d in lin["daily_cashflow"] if d["investment_value"]]
    assert pl == sorted(pl) and pl[-1] > pl[0]
    assert lin["virtual_investment_profit_loss"] > 0


def test_delta_vs_no_purchase_and_choice_outflow():
    paths = run()
    assert all(v == 0 for v in paths["no_purchase"]["balance_delta_vs_no_purchase"].values())
    assert paths["purchase_now"]["balance_delta_vs_no_purchase"]["14"] == -300000
    assert paths["purchase_now"]["choice_cash_outflow"] == 300000
    assert paths["no_purchase"]["choice_cash_outflow"] == 0


def test_swagger_examples_are_valid_and_documented():
    schema = client.get("/openapi.json").json()["components"]["schemas"]["WorldCompareRequest"]
    for ex in schema["examples"]:
        assert client.post("/api/v4/scenarios/compare", json=ex).status_code == 200

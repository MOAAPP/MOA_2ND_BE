"""Stage 6 integration assertions against the immutable original 20-user ZIP."""
import json
from datetime import date
from fastapi.testclient import TestClient

from app.main import app
from app.services.dataset import load_dataset
from app.services.stage6 import evaluate, spend_capacity, detect_changes

client = TestClient(app)
DAY = "2026-11-04"


def test_canonical_dataset_and_main_user():
    data = load_dataset()
    assert len(data.profiles) == 20
    assert sorted(data.profiles) == [f"S{i:02d}" for i in range(1, 21)]
    assert data.meta["snapshot_date"] == DAY
    assert client.get("/api/v4/demo/default").json()["user_id"] == "S01"
    assert client.get("/api/v4/users").json()["users"] == sorted(data.profiles)


def test_all_personas_have_home_and_are_user_isolated():
    balances = {}
    for i in range(1, 21):
        uid = f"S{i:02d}"
        response = client.get(f"/api/v4/users/{uid}/home?as_of={DAY}")
        assert response.status_code == 200, (uid, response.text[:500])
        item = response.json()
        assert item["user_id"] == uid
        assert item["is_synthetic"] is True
        assert item["as_of_date"] == DAY
        assert isinstance(item["available_cash"], int)
        assert len(item["daily_cashflow"]) == 14
        assert all(t["user_id"] == uid for t in item["recent_transactions"])
        balances[uid] = item["available_cash"]
    assert len(set(balances.values())) > 1


def test_capacity_and_briefing_from_engine(tmp_path, monkeypatch):
    import app.services.stage6 as s6
    monkeypatch.setattr(s6, "RUNTIME", tmp_path)
    resp = client.get(f"/api/v4/users/S01/briefing?as_of={DAY}")
    assert resp.status_code == 200, resp.text
    b = resp.json()
    assert b["llm_used"] is False
    assert b["generation_mode"] == "rule_template"
    assert b["change_detection"]["status"] == "first_analysis"
    cap = b["spending_capacity"]
    assert cap["current_cash"] == client.get(f"/api/v4/users/S01/home?as_of={DAY}").json()["available_cash"]
    assert cap["estimated_additional_spend"] >= 0
    assert cap["predictive_not_permission"] is True
    second = client.get(f"/api/v4/users/S01/briefing?as_of={DAY}")
    assert second.json()["change_detection"]["status"] == "unchanged"
    assert (tmp_path/"S01"/"analyses"/(DAY+".json")).is_file()
    assert client.get(f"/api/v4/users/S02/briefing?as_of={DAY}").json()["user_id"] == "S02"


def test_as_of_isolation_and_future_oracle_protection(tmp_path, monkeypatch):
    import app.services.stage6 as s6
    monkeypatch.setattr(s6, "RUNTIME", tmp_path)
    past = client.get("/api/v4/users/S01/home?as_of=2026-11-03")
    current = client.get("/api/v4/users/S01/home?as_of=2026-11-04")
    assert past.status_code == current.status_code == 200
    old_ids = {t["id"] for t in past.json()["recent_transactions"]}
    assert all(t["date"] <= "2026-11-03" for t in past.json()["recent_transactions"])
    assert all(t["date"] <= DAY for t in current.json()["recent_transactions"])
    assert not any(t.get("future_scenario") for t in current.json()["recent_transactions"])
    assert client.get("/api/v4/users/S01/home?as_of=2026-11-05").status_code == 422
    client.get("/api/v4/users/S01/briefing?as_of=2026-11-03")
    changed = client.get(f"/api/v4/users/S01/briefing?as_of={DAY}").json()
    assert changed["change_detection"]["status"] in ("changed", "unchanged")
    assert changed["change_detection"]["previous_as_of_date"] == "2026-11-03"


def test_explicit_credit_date_and_missing_user_errors():
    assert client.get(f"/api/v4/users/S99/briefing?as_of={DAY}").status_code == 404
    assert client.get(f"/api/v4/users/S01/spending-capacity?as_of={DAY}&payment_method=credit").status_code == 422
    credit = client.get(f"/api/v4/users/S01/spending-capacity?as_of={DAY}&payment_method=credit&credit_debit_date=2026-11-10")
    assert credit.status_code == 200, credit.text[:500]
    assert credit.json()["calculation_method"] == "python_engine_binary_search_v1"


def test_news_never_fabricated_and_plans_isolated():
    assert client.get("/api/v4/users/S01/assets/news").json()["articles"] == []
    assert client.get("/api/v4/users/S01/plans").json()["user_id"] == "S01"


def test_change_detector_facts_not_llm():
    facts = {"user_id": "S01", "as_of_date": DAY, "observed_transaction_ids": [],
             "minimum_balance": 0, "essential_payments": [], "forecast_total": 12000,
             "virtual_investments": {}, "savings_goals": []}
    assert detect_changes(None, facts)["status"] == "first_analysis"
    assert detect_changes(facts, facts)["status"] == "unchanged"
    now = {**facts, "observed_transaction_ids": ["new-tx"],
           "minimum_balance": -2000, "forecast_total": 15000}
    changed = detect_changes(facts, now)
    assert changed["status"] == "changed"
    assert {x["code"] for x in changed["events"]} >= {
        "new_transactions", "minimum_balance_decreased", "variable_forecast_increased"}


def test_saved_world_is_immutable_and_revalidated_on_observed_next_day(tmp_path, monkeypatch):
    import app.api.stage3 as stage3
    import app.api.agent as agent
    monkeypatch.setattr(stage3, "STORAGE_DIR", tmp_path / "worlds")
    monkeypatch.setattr(agent, "SAVE_DIR", tmp_path / "agent")
    payload = {
        "request": {
            "user_id": "S01", "as_of": "2026-11-03",
            "purchase_amount": 1000, "purchase_date": "2026-11-05",
            "horizon_days": 14, "minimum_required_balance": 0,
        },
        "selected_scenario_id": "no_purchase",
        "label": "S01 미래 관측 테스트",
    }
    create = client.post("/api/v4/scenarios/save", json=payload)
    assert create.status_code == 200, create.text[:500]
    sid = create.json()["id"]
    path = stage3.STORAGE_DIR / (sid + ".json")
    before = path.read_bytes()
    saved = json.loads(before)
    assert saved["selected_scenario_id"] == "no_purchase"
    assert saved["result_snapshot"]["user_id"] == "S01"
    assert saved["forecast_snapshot"]["forecast_as_of"] == "2026-11-03"
    listed = client.get("/api/v4/users/S01/plans").json()
    assert sid in [plan["id"] for plan in listed["plans"]]
    assert sid not in [plan["id"] for plan in client.get("/api/v4/users/S02/plans").json()["plans"]]
    tracked = client.get(f"/api/v4/users/S01/plans/{sid}/tracking?as_of=2026-11-04")
    assert tracked.status_code == 200, tracked.text[:500]
    result = tracked.json()
    assert result["snapshot_preserved"] is True
    assert result["revalidation"]["status"] in ("valid", "constraints_broken", "needs_revision")
    assert len(result["daily_spend_comparison"]) == 1
    assert path.read_bytes() == before
    assert client.get(f"/api/v4/users/S02/plans/{sid}/tracking?as_of=2026-11-04").status_code == 404

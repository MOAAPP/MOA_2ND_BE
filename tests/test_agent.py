"""Stage 5 integration: real ZIP, 20 users, date safety, strict feasibility."""
from datetime import date
from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services.dataset import load_dataset
from app.services.agent_paths import GoalConditions, SearchRequest, search_paths
from app.services.agent_llm import AgentFailure, execute_tool

client = TestClient(app)
DAY = date(2026, 11, 4)


def payload(uid="S01", **kwargs):
    return {"conditions": {"user_id": uid, "as_of": DAY.isoformat(),
                           "purchase_required": False, **kwargs},
            "horizon_days": 14, "max_evaluations": 6}


def test_dataset_audit_has_actual_20_people_and_explicit_main():
    r = client.get("/api/v4/agent/dataset/audit")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["valid"] is True
    assert data["user_count"] == 20
    assert data["user_ids"] == [f"S{i:02d}" for i in range(1, 21)]
    assert data["default_user_id"] == "S01"
    assert data["collection_counts"]["daily"] == 1840


def test_search_uses_canonical_engine_and_never_recommends_invalid_path():
    r = client.post("/api/v4/agent/search", json=payload(purchase_amount=10000))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["user_id"] == "S01"
    assert d["evaluated_count"] <= 6
    assert len(d["routes"]) <= 4
    for route in d["routes"]:
        assert route["hard_constraints_met"]
        assert all(route["checks"].values())
        assert route["minimum_balance"] >= 0
        assert route["scenario"]["status"] == "calculated"
        assert all(e["user_id"] == "S01" for e in route["scenario"]["events"])


def test_hard_minimum_balance_is_never_silently_relaxed():
    r = client.post("/api/v4/agent/search",
                    json=payload(purchase_amount=10000, minimum_balance=100_000_000))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "no_valid_route"
    assert r.json()["routes"] == []


def test_unconfirmed_savings_goal_returns_confirmation_not_fake_amount():
    r = client.post("/api/v4/agent/search", json=payload(savings_goal_id="travel"))
    assert r.status_code == 200
    assert r.json()["status"] == "needs_confirmation"
    assert r.json()["routes"] == []


def test_missing_purchase_amount_requires_confirmation_when_hard():
    ds = load_dataset()
    g = GoalConditions(user_id="S01", as_of=DAY, purchase_required=True,
                       target_product_id="missing-id")
    with pytest.raises(ValueError, match="purchase_candidate_not_found"):
        search_paths(ds, SearchRequest(conditions=g, horizon_days=14, max_evaluations=2))


def test_no_cross_user_routes_and_no_future_observation():
    for uid in ["S02", "S05", "S20"]:
        resp = client.post("/api/v4/agent/search", json=payload(uid, purchase_amount=5000))
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["user_id"] == uid
        for route in result["routes"]:
            assert all(event["user_id"] == uid for event in route["scenario"]["events"])
    home = client.get("/api/v4/users/S01/home?as_of=2026-11-03")
    assert home.status_code == 200
    assert all(tx["date"] <= "2026-11-03" for tx in home.json()["recent_transactions"])


def test_engine_tool_whitelist_and_identity_guard():
    ds = load_dataset()
    with pytest.raises(AgentFailure, match="tool_not_allowed"):
        execute_tool(ds, "eval", {"user_id": "S01", "as_of": DAY.isoformat()}, "S01", DAY)
    with pytest.raises(AgentFailure, match="cross_user_or_date"):
        execute_tool(ds, "get_financial_state",
                     {"user_id": "S02", "as_of": DAY.isoformat()}, "S01", DAY)
    result = execute_tool(ds, "get_financial_state",
                          {"user_id": "S01", "as_of": DAY.isoformat()}, "S01", DAY)
    assert result["available_cash"] == 27800


def test_analyze_without_credentials_keeps_default_worlds(monkeypatch):
    monkeypatch.delenv("MOA_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = client.post("/api/v4/agent/analyze",
                    json={"user_id": "S01", "as_of": DAY.isoformat(),
                          "goal": "월세를 꼭 내고 싶어요"})
    assert r.status_code == 503
    assert r.json()["detail"]["basic_parallel_worlds_unchanged"] is True
    normal = client.post("/api/v4/scenarios/compare", json={
        "user_id": "S01", "purchase_amount": 1000
    })
    assert normal.status_code == 200, normal.text


def test_wrong_user_and_out_of_range_day_are_rejected():
    assert client.post("/api/v4/agent/search",
                       json=payload("S99")).status_code == 404
    future = payload("S01")
    future["conditions"]["as_of"] = "2026-11-05"
    assert client.post("/api/v4/agent/search", json=future).status_code == 422


def test_routes_save_requires_revalidated_recommendation(monkeypatch, tmp_path):
    import app.api.agent as api
    monkeypatch.setattr(api, "SAVE_DIR", tmp_path)
    p = payload(purchase_amount=10000)
    r = client.post("/api/v4/agent/search", json=p)
    assert r.status_code == 200
    routes = r.json()["routes"]
    if not routes:
        assert r.json()["status"] == "no_valid_route"
        return
    saved = client.post("/api/v4/agent/routes/save", json={
        "search_request": p, "route_id": routes[0]["id"], "label": "테스트 경로",
    })
    assert saved.status_code == 200, saved.text
    sid = saved.json()["id"]
    assert client.get(f"/api/v4/agent/routes/{sid}?user_id=S02").status_code == 404
    assert client.get(f"/api/v4/agent/routes/{sid}?user_id=S01").status_code == 200
    rerun = client.post(f"/api/v4/agent/routes/{sid}/recalculate?user_id=S01")
    assert rerun.status_code == 200, rerun.text
    assert rerun.json()["still_valid"] is True

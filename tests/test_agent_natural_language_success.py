"""Mock LLM parsing + live Python finance engine: verify both feasible and unsafe horizons."""
import json
from types import SimpleNamespace
from fastapi.testclient import TestClient
from app.main import app
from app.services import agent_llm

client = TestClient(app)
GOAL = "2026년 11월 5일에 1000원짜리 간식을 살까 고민 중이야. 꼭 살 필요는 없어. 가능하면 구매·미구매 경로를 알려줘."

class Message:
    def __init__(self, calls):
        self.tool_calls = calls
    def model_dump(self, exclude_none=True):
        return {"role": "assistant", "tool_calls": [
            {"id": x.id, "type": "function", "function": {
                "name": x.function.name, "arguments": x.function.arguments,
            }} for x in self.tool_calls
        ]}

def response(name=None, arguments=None):
    calls = [SimpleNamespace(id="call_" + name, function=SimpleNamespace(
        name=name, arguments=json.dumps(arguments)))] if name else []
    return SimpleNamespace(choices=[SimpleNamespace(message=Message(calls))],
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10))

def fake_provider(monkeypatch):
    replies = iter([
        response("set_financial_conditions", {
            "purchase_amount": 1000, "purchase_required": False,
            "preferred_purchase_date": "2026-11-05",
            "allowed_payment_methods": ["debit"],
            "hard_constraints": [], "soft_preferences": ["간식 구매 고민"],
            "confirmations_required": [],
        }),
        response("compare_scenarios", {
            "user_id": "S06", "as_of": "2026-11-04", "purchase_amount": 1000,
        }),
        response(),
    ])
    monkeypatch.setattr(agent_llm, "_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: next(replies),
        )),
    ))

def request(horizon=None):
    body = {"user_id": "S06", "as_of": "2026-11-04", "goal": GOAL, "max_evaluations": 30}
    if horizon is not None:
        body["horizon_days"] = horizon
    return body

def test_natural_language_success_for_14_days(monkeypatch):
    fake_provider(monkeypatch)
    result = client.post("/api/v4/agent/analyze", json=request(14))
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "ok", body
    assert body["conditions"]["purchase_amount"] == 1000
    assert body["tool_execution_log"][0]["status"] == "ok"
    assert body["tool_success_rate"] == 1.0
    search = body["search"]
    assert search["status"] == "ok"
    assert search["valid_count"] >= len(search["routes"]) > 0
    assert search["no_route_guidance"] is None
    for route in search["routes"]:
        assert route["hard_constraints_met"]
        assert all(route["checks"].values())
        assert route["minimum_balance"] >= 0
        assert route["scenario"]["essential_payments_covered"]
        assert len(route["scenario"]["daily_cashflow"]) == 14

def test_default_30_days_remains_unsafe(monkeypatch):
    fake_provider(monkeypatch)
    result = client.post("/api/v4/agent/analyze", json=request())
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "no_valid_route", body
    assert not body["search"]["routes"]
    guide = body["search"]["no_route_guidance"]
    assert guide["reason"] == "baseline_cashflow_at_risk"
    assert guide["baseline_no_purchase"]["horizon_days"] == 30

def test_tool_and_optimizer_use_same_selected_horizon(monkeypatch):
    fake_provider(monkeypatch)
    seen = []
    real = agent_llm.compare_worlds
    def spy(ds, payload):
        seen.append(payload.horizon_days)
        return real(ds, payload)
    monkeypatch.setattr(agent_llm, "compare_worlds", spy)
    response = client.post("/api/v4/agent/analyze", json=request(14))
    assert response.status_code == 200, response.text
    assert seen == [14]

def test_horizon_bounds():
    assert client.post("/api/v4/agent/analyze", json=request(13)).status_code == 422
    assert client.post("/api/v4/agent/analyze", json=request(91)).status_code == 422

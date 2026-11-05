"""Stage8 no-secret integration: provider configuration, live overlays, quiz and briefing.

Real upstream API calls are deliberately mocked here. Passing does not establish
live provider credentials, image recognition accuracy, or financial prediction accuracy.
"""
from __future__ import annotations
import io
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services.dataset import load_dataset
from app.services.observed_view import for_user_date
from app.services.ai_briefing import ai_briefing
from app.services.learning_ai import _topic

client = TestClient(app)
KST = ZoneInfo("Asia/Seoul")

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MOA_RUNTIME_DIR", str(tmp_path))
    for k in ("MOA_AI_API_KEY", "OPENAI_API_KEY", "MOA_AGENT_API_KEY",
              "MOA_VISION_API_KEY", "MOA_AI_BASE_URL", "MOA_VISION_BASE_URL"):
        monkeypatch.delenv(k, raising=False)

def test_status_only_returns_boolean_not_credentials(monkeypatch):
    monkeypatch.setenv("MOA_AI_API_KEY", "secret-should-never-appear-in-response")
    result = client.get("/api/v4/ai/status")
    assert result.status_code == 200
    assert result.json()["llm_configured"]
    assert result.json()["keys_exposed"] is False
    assert "secret-" not in result.text

def test_daily_quiz_isolated_and_answered_once():
    day = "2026-11-05"
    q1 = client.get("/api/v4/users/S01/daily-quiz", params={"day": day})
    q2 = client.get("/api/v4/users/S02/daily-quiz", params={"day": day})
    assert q1.status_code == 200, q1.text
    assert q2.status_code == 200, q2.text
    assert q1.json()["source"] == "rule_based"
    assert q1.json()["user_id"] != q2.json()["user_id"]
    assert q1.json()["correct_answer"] is None
    item = q1.json()
    resp = client.post(f"/api/v4/users/S01/daily-quiz/answer?day={day}",
                       json={"quiz_id": item["id"], "choice": 0})
    assert resp.status_code == 200, resp.text
    assert resp.json()["correct_answer"] in range(4)
    assert resp.json()["explanation"]
    assert client.post(f"/api/v4/users/S01/daily-quiz/answer?day={day}",
                       json={"quiz_id": item["id"], "choice": 0}).status_code == 409
    assert client.post(f"/api/v4/users/S02/daily-quiz/answer?day={day}",
                       json={"quiz_id": item["id"], "choice": 0}).status_code == 422
    assert client.get("/api/v4/users/S02/daily-quiz", params={"day": day}).json()["answer_record"] is None

def test_wrong_answer_priority_is_persona_specific():
    rows = [{"topic": "credit", "answer_record": {"choice": 0, "correct": False}, "date": "2026-11-04"}]
    topic, reason = _topic("S01", "2026-11-05", rows)
    assert topic == "credit"
    assert reason == "recent_wrong_answer"

def test_grounded_llm_explanation_does_not_replace_python_money(monkeypatch):
    ds = load_dataset()
    canonical = ai_briefing(ds, "S01", date(2026, 11, 4))
    assert canonical["generation_mode"] == "rule_template"
    assert canonical["llm_used"] is False
    with patch("app.services.ai_briefing.configured", return_value=True), \
         patch("app.services.ai_briefing._explain", return_value="결제일에 대비해 예상 잔액을 살펴보세요."):
        enriched = ai_briefing(ds, "S01", date(2026, 11, 4))
    assert enriched["llm_used"] is True
    assert enriched["spending_capacity"] == canonical["spending_capacity"]
    assert enriched["generation_mode"] == "llm_grounded_explanation"

def test_post_snapshot_confirmed_transaction_is_consistent_across_home_forecast_briefing():
    ds = load_dataset()
    uid = "S01"
    account = ds.get_user(uid).profile["payment_account_id"]
    original = client.get("/api/v4/users/S01/home?as_of=2026-11-04").json()
    assert original["available_cash"] == 27800
    stamp = int(datetime(2026, 11, 5, 10, 0, tzinfo=KST).timestamp() * 1000)
    from app.services.stage2_store import mutate_user
    def add(data):
        data["transactions"].append({
            "source_id": "test-confirmed", "fingerprint": "test-confirmed",
            "effective_date": "2026-11-05",
            "finance_row": {
                "id": "sms:test-confirmed", "user_id": uid, "account_id": account,
                "occurred_at": stamp, "debit_at": stamp, "amount": 2000,
                "name": "검증 결제", "kind": "spend", "method": "debit",
                "status": "confirmed_user", "essential": False,
            },
        })
    mutate_user(uid, add)
    assert client.get("/api/v4/users/S01/home?as_of=2026-11-04").json()["available_cash"] == 27800
    changed = client.get("/api/v4/users/S01/home?as_of=2026-11-05")
    assert changed.status_code == 200, changed.text
    live = changed.json()
    assert live["available_cash"] == 25800
    assert live["observation_end"] == "2026-11-04"
    assert live["financial_state_status"] == "partial_post_snapshot"
    assert any(t["id"] == "sms:test-confirmed" for t in live["recent_transactions"])
    assert client.get("/api/v4/users/S02/home?as_of=2026-11-04").json()["available_cash"] == 118400
    forecast = client.get("/api/v4/users/S01/forecast?as_of=2026-11-05")
    assert forecast.status_code == 200, forecast.text
    assert forecast.json()["forecast_as_of"] == "2026-11-05"
    assert all(x["date"] > "2026-11-05" for x in forecast.json()["daily"])
    briefing = client.get("/api/v4/users/S01/briefing?as_of=2026-11-05")
    assert briefing.status_code == 200, briefing.text
    assert briefing.json()["spending_capacity"]["current_cash"] == 25800
    assert for_user_date(ds, "S01", date(2026, 11, 4)) is ds
    assert ds.meta["snapshot_date"] == "2026-11-04"

def test_future_is_not_imported_as_observed():
    response = client.get("/api/v4/users/S01/home?as_of=2026-12-11")
    assert response.status_code == 422
    assert client.get("/api/v4/users/S01/dataset-snapshot?as_of=2026-11-05").status_code == 422

def test_provider_vision_mocks_not_accuracy(monkeypatch):
    monkeypatch.setenv("MOA_VISION_API_KEY", "fake-test-key")
    monkeypatch.setenv("MOA_VISION_BASE_URL", "https://example.invalid/v1")
    class Response:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": json.dumps({
                "product_name": "시험 상품", "selling_price": 12000,
                "quantity": 1, "option_extra_cost": 0, "shipping_fee": 0,
            })}}], "usage": {"prompt_tokens": 10, "completion_tokens": 30}}
    class MockClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs):
            assert url == "https://example.invalid/v1/chat/completions"
            assert kwargs["headers"]["Authorization"] == "Bearer fake-test-key"
            return Response()
    image = Image.new("RGB", (12, 12))
    buf = io.BytesIO()
    image.save(buf, "PNG")
    with patch("app.services.stage2_vision.httpx.AsyncClient", return_value=MockClient()):
        result = client.post("/api/v4/vision/analyze",
                             data={"user_id": "S01", "privacy_acknowledged": "true"},
                             files={"file": ("test.png", buf.getvalue(), "image/png")})
    assert result.status_code == 200, result.text
    assert result.json()["analysis_status"] == "succeeded"
    assert result.json()["estimated_checkout_total"] == 12000
    assert result.json()["checkout_total_confirmed"] is False

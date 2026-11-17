"""Regression tests for FactChat-compatible briefing, agent and multimodal outputs.

External LLMs are mocked: deployment still requires a separate real API smoke test.
"""
from __future__ import annotations

import asyncio
import io
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

from app.services.ai_briefing import _explain, _verified_explanation
from app.services.ai_provider import AiUnavailable
from app.services.agent_llm import parse_conditions
from app.services import stage2_vision
from app.services.stage2_vision import VisionProblem


def test_briefing_keeps_first_complete_polite_sentence_from_long_claude_response():
    text = (
        "다가오는 필수 결제에 필요한 계좌 잔액을 먼저 확인해 보세요. "
        "금융 상태에 대한 분석은 예측을 포함하므로 이번 달 지출과 저축 계획을 살펴보고 "
        "변경된 현금 흐름도 계속 점검해 주세요."
    )
    assert len(text) > 100
    assert _verified_explanation(text) == "다가오는 필수 결제에 필요한 계좌 잔액을 먼저 확인해 보세요."


def test_briefing_retries_once_for_wrong_field_and_uses_verified_text():
    _explain.cache_clear()
    with patch("app.services.ai_briefing.structured_text", side_effect=[
        ({"summary": "다른 필드"}, "claude"),
        ({"explanation": "다음 결제에 필요한 금액을 먼저 확인해 보세요."}, "claude"),
    ]) as model:
        text = _explain('{"upcoming_essential_payment":"covered"}', "claude")
    _explain.cache_clear()
    assert text.endswith("보세요.")
    assert model.call_count == 2
    assert "70자" in model.call_args_list[1].args[0]


def test_briefing_never_accepts_invented_numbers_even_after_retry():
    _explain.cache_clear()
    bad = {"explanation": "이번 달에는 999999원을 자유롭게 써도 괜찮아요."}
    with patch("app.services.ai_briefing.structured_text", return_value=(bad, "claude")) as model:
        with pytest.raises(AiUnavailable, match="invalid_grounded_explanation"):
            _explain('{"additional_spending_capacity":"none"}', "claude")
    _explain.cache_clear()
    assert model.call_count == 2


def _mock_completion(*, tool_calls=None, content=None, incoming=10, outgoing=6):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=tool_calls, content=content))],
        usage=SimpleNamespace(prompt_tokens=incoming, completion_tokens=outgoing),
    )


def test_agent_falls_back_to_validated_factchat_json_when_tool_call_is_missing(monkeypatch):
    monkeypatch.setenv("MOA_AI_BASE_URL", "https://factchat-cloud.mindlogic.ai/v1/gateway")
    responses = iter([
        _mock_completion(tool_calls=[]),
        _mock_completion(content='```json\n{"purchase_amount":7000,"purchase_required":false,"allowed_payment_methods":["debit"],"confirmations_required":[]}\n```'),
    ])
    requested = []
    def create(**kwargs):
        requested.append(kwargs)
        return next(responses)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    conditions, tokens = parse_conditions(
        client, "claude-haiku-4-5-20251001",
        "7000원짜리 가방을 살까 고민이에요", "S01", date(2026, 11, 4),
    )
    assert conditions.purchase_amount == 7000
    assert conditions.purchase_required is False
    assert conditions.user_id == "S01"
    assert tokens == {"input_tokens": 20, "output_tokens": 12}
    assert "tools" in requested[0]
    assert "tools" not in requested[1]


def test_agent_drops_amount_not_present_in_user_goal_after_json_fallback(monkeypatch):
    monkeypatch.setenv("MOA_AI_BASE_URL", "https://factchat-cloud.mindlogic.ai/v1/gateway")
    responses = iter([
        _mock_completion(tool_calls=[]),
        _mock_completion(content=json.dumps({"purchase_amount": 99000, "purchase_required": True})),
    ])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: next(responses))))
    conditions, _ = parse_conditions(client, "claude", "이 운동화를 꼭 사고 싶어요", "S01", date(2026, 11, 4))
    assert conditions.purchase_amount is None
    assert any("purchase_amount" in t for t in conditions.confirmations_required)


def test_vision_factchat_accepts_fenced_multimodal_json_without_unsupported_format(monkeypatch):
    monkeypatch.setenv("MOA_AI_API_KEY", "fake-test-key")
    monkeypatch.setenv("MOA_VISION_BASE_URL", "https://factchat-cloud.mindlogic.ai/v1/gateway")
    monkeypatch.setenv("MOA_VISION_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setattr(stage2_vision, "read_user", lambda uid: {"analyses": []})
    saved = []
    monkeypatch.setattr(stage2_vision, "mutate_user", lambda uid, add: saved.append(uid))
    captured = {}
    class Response:
        status_code = 200
        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {
                "content": '```json\n{"product_name":"가상 테스트 상품","selling_price":12000,"quantity":1,"option_extra_cost":0,"shipping_fee":0}\n```'
            }}], "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["payload"] = kwargs["json"]
            return Response()
    monkeypatch.setattr(stage2_vision.httpx, "AsyncClient", lambda **kw: Client())
    image = Image.new("RGB", (24, 24), "white")
    stream = io.BytesIO()
    image.save(stream, "PNG")
    result = asyncio.run(stage2_vision.analyze_image("S01", stream.getvalue(), "image/png"))
    assert result.product.product_name == "가상 테스트 상품"
    assert result.estimated_checkout_total == 12000
    assert result.checkout_total_confirmed is False
    assert result.product.needs_user_confirmation is True
    assert saved == ["S01"]
    assert captured["url"].endswith("/chat/completions")
    assert captured["payload"]["model"] == "claude-haiku-4-5-20251001"
    assert "response_format" not in captured["payload"]


def test_vision_rejects_truncated_response_without_fake_success(monkeypatch):
    monkeypatch.setenv("MOA_AI_API_KEY", "fake-test-key")
    monkeypatch.setenv("MOA_VISION_BASE_URL", "https://factchat-cloud.mindlogic.ai/v1/gateway")
    monkeypatch.setattr(stage2_vision, "read_user", lambda uid: {"analyses": []})
    class Response:
        status_code = 200
        def json(self):
            return {"choices": [{"finish_reason": "length", "message": {"content": '{"product_name":"bad'}}]}
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs): return Response()
    monkeypatch.setattr(stage2_vision.httpx, "AsyncClient", lambda **kw: Client())
    image = Image.new("RGB", (24, 24), "white")
    stream = io.BytesIO()
    image.save(stream, "PNG")
    with pytest.raises(VisionProblem, match="AI 응답이 중간에 끊겼습니다"):
        asyncio.run(stage2_vision.analyze_image("S01", stream.getvalue(), "image/png"))


def test_agent_drops_invented_product_candidate_id_that_would_cause_422(monkeypatch):
    monkeypatch.setenv("MOA_AI_BASE_URL", "https://factchat-cloud.mindlogic.ai/v1/gateway")
    call = SimpleNamespace(function=SimpleNamespace(
        name="set_financial_conditions",
        arguments=json.dumps({
            "target_product_id": "invented-shoe-candidate",
            "purchase_amount": 7000,
            "purchase_required": False,
            "allowed_payment_methods": ["debit"],
        }),
    ))
    reply = _mock_completion(tool_calls=[call])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: reply)))
    conditions, _ = parse_conditions(
        client, "claude-haiku-4-5-20251001",
        "7000원짜리 운동화를 살까 고민이에요", "S01", date(2026, 11, 4),
    )
    assert conditions.purchase_amount == 7000
    assert conditions.target_product_id is None


def test_vision_extracts_json_after_claude_prose_and_validates_price():
    fields = stage2_vision._validated_vision_output(
        'Here is the product information:\n```json\n'
        '{"product":{"product_name":"데모 가방","selling_price":"12,000원",'
        '"quantity":1,"shipping_fee":0,"option_extra_cost":0}}\n```'
    )
    assert fields.product_name == "데모 가방"
    assert fields.selling_price == 12000
    assert fields.needs_user_confirmation is True
    assert stage2_vision.checkout_total(fields) == 12000


def test_vision_discards_only_unverifiable_optional_evidence():
    fields = stage2_vision._validated_vision_output(json.dumps({
        "product_name": "가방", "selling_price": 12000,
        "visual_evidence_regions": [{"field": "price", "region": {"x": 999}}],
        "needs_user_confirmation": False,
    }))
    assert fields.product_name == "가방"
    assert fields.selling_price == 12000
    assert "visual_evidence_regions" in fields.uncertain_fields
    assert fields.needs_user_confirmation is True


def test_vision_never_reports_success_for_unreadable_empty_response():
    with pytest.raises(VisionProblem, match="이미지에서 구매 상품을 구분하지 못했습니다"):
        stage2_vision._validated_vision_output('{"product_name":null,"selling_price":null}')

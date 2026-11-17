"""Privacy-safe finance AI language quality regression tests.

No external model request is made here; live correctness requires separate staging evaluation.
"""
import pytest
from unittest.mock import patch
from app.services.ai_quality import FINANCIAL_AI_V10_POLICY, polite_sentences
from app.services.ai_briefing import _explain
from app.services.ai_provider import AiUnavailable


@pytest.mark.parametrize("text,expected", [
    ("결제 예정일에 필요한 현금을 먼저 확인해 보세요.", True),
    ("지금은 구매를 미루는 것이 안전할 수 있습니다.", True),
    ("이번 주에는 소비 기록을 다시 살펴볼까요?", True),
    ("계좌가 부족할 수 있으니 확인해 주세요. 지출 일정을 검토해 보세요.", True),
    ("그냥 사도 돼.", False),
    ("니가 알아서 해.", False),
    ("구매해도 괜찮아!", False),
    ("결제 금액을 확인해 보세요. 그냥 사.", False),
])
def test_polite_sentences(text, expected):
    assert polite_sentences(text) is expected


def test_policy_contains_ten_review_steps():
    assert all(f"{i}." in FINANCIAL_AI_V10_POLICY for i in range(1, 11))
    assert "존댓말" in FINANCIAL_AI_V10_POLICY


def test_briefing_rejects_informal_llm_output():
    _explain.cache_clear()
    with patch("app.services.ai_briefing.structured_text", return_value=(
        {"explanation": "이번에는 그냥 사고 다음에 생각해."}, "mock"
    )):
        with pytest.raises(AiUnavailable, match="explanation_not_polite"):
            _explain('{"additional_spending_capacity":"none"}', "mock")
    _explain.cache_clear()


def test_briefing_accepts_grounded_polite_llm_output():
    _explain.cache_clear()
    with patch("app.services.ai_briefing.structured_text", return_value=(
        {"explanation": "다가오는 결제에 필요한 잔액을 먼저 확인해 보세요."}, "mock"
    )):
        assert "확인해 보세요" in _explain('{"additional_spending_capacity":"none"}', "mock")
    _explain.cache_clear()

"""LLM prose must not carry amounts: Python renders every financial number."""
import pytest

from app.services.ai_briefing import _verified_explanation
from app.services.ai_quality import has_numeric_content


@pytest.mark.parametrize("text,expected", [
    ("다가오는 결제를 위해 3만 원을 남겨 두세요.", True),
    ("다가오는 결제를 위해 삼만원을 남겨 두세요.", True),
    ("오천 원 정도는 괜찮아요.", True),
    ("지출을 십 퍼센트 줄여 보세요.", True),
    ("１０％ 줄여 보세요.", True),
    ("₩ 표시는 쓰지 않아요.", True),
    ("이번 주 결제 일정을 먼저 확인해 보세요.", False),
    ("한 번 더 생각해 보시는 걸 권해요.", False),
    ("이만큼이면 충분히 여유가 있어요.", False),
])
def test_numeric_content(text, expected):
    assert has_numeric_content(text) is expected


def test_briefing_explanation_rejects_korean_spelled_amounts():
    assert _verified_explanation("다가오는 결제를 위해 삼만원을 남겨 두세요.") is None
    assert _verified_explanation("다가오는 필수 결제를 고려해 지금의 소비 계획을 살펴보세요.") is not None

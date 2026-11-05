"""Only qualitative, verified facts reach the explanatory LLM; Python owns all numbers."""
import json
from datetime import date
from unittest.mock import patch

from app.services.ai_briefing import ai_briefing, _explain
from app.services.dataset import load_dataset


def test_briefing_sends_no_numeric_identifiers_amounts_or_dates(monkeypatch):
    ds = load_dataset()
    seen = {}
    def fake_provider(system, facts, *, model, max_tokens):
        seen["facts"] = facts
        return {"explanation": "예정된 지출을 고려해 다음 결제에 필요한 여유를 우선 확인해 보세요."}, model
    _explain.cache_clear()
    with patch("app.services.ai_briefing.configured",return_value=True), \\
         patch("app.services.ai_briefing.structured_text",side_effect=fake_provider):
        result = ai_briefing(ds,"S01",date(2026,11,4))
    _explain.cache_clear()
    assert result["llm_used"] is True
    assert result["generation_mode"] == "llm_grounded_explanation"
    assert result["spending_capacity"]["current_cash"] == 27800
    assert "available_cash" not in seen["facts"]
    assert "user_id" not in seen["facts"]
    assert "date" not in seen["facts"]
    assert "essential_payment" not in seen["facts"]
    facts_json = json.dumps(seen["facts"],ensure_ascii=False)
    assert "27800" not in facts_json
    assert "2026" not in facts_json
    assert "S01" not in facts_json
    assert seen["facts"]["additional_spending_capacity"] == "none"
    assert seen["facts"]["future_expenses_are_predictions"] is True

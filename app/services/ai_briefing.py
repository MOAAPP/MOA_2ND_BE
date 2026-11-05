"""Grounded explanatory briefing; all financial numbers originate from Python."""
from __future__ import annotations
import json
import os
import re
from functools import lru_cache
from app.services.ai_provider import AiUnavailable, configured, structured_text
from app.services.stage6 import briefing

_PROMPT = """너는 한국어로 개인 금융 흐름의 의미를 설명하는 친절한 서비스 안내 AI다.
입력은 실제 은행 기록이 아닌 합성 체험 데이터를 Python 엔진이 분석한 정성적 상태 신호다.
여기에 없는 금액·날짜·거래·원인을 절대로 만들거나 추측하지 마라.
모든 구체적인 금액과 날짜는 별도 Python UI가 표시한다.
설명에는 숫자(한글로 쓴 개수도 포함), 날짜, 금액, 수익 약속, 투자 권유, 소비 허가를 넣지 마라.
정성적 위험 신호가 있으면 결제와 잔액을 확인하도록 권하고, 없으면 위험이 있다고 단정하지 마라.
입력에 확정된 금융 변화가 없으면 변화가 발생했다고 단정하지 마라.
개발자 용어, 머신러닝 모델명, 코드, JSON 필드명을 본문에 나열하지 마라.
출력 예시: {"explanation":"다가오는 필수 결제를 고려해 지금의 소비 계획을 신중하게 살펴보세요."}
오직 {"explanation":"자연스럽고 짧은 한국어 안내 문장"} 형태의 JSON 객체만 출력하라."""

@lru_cache(maxsize=256)
def _explain(facts_json: str, model: str) -> str:
    result, _ = structured_text(_PROMPT, json.loads(facts_json), model=model, max_tokens=160)
    text = result.get("explanation")
    if not isinstance(text, str) or not 10 <= len(text.strip()) <= 100:
        raise AiUnavailable("invalid_grounded_explanation")
    # Financial numbers are only displayed by deterministic Python widgets.
    if re.search(r"[0-9０-９]|\d|[%₩$]", text):
        raise AiUnavailable("explanation_contains_unverified_number")
    return text.strip()

def ai_briefing(ds, user_id, day, *, minimum_balance=0):
    data = briefing(ds, user_id, day, minimum_balance=minimum_balance)
    data["llm_model"] = None
    data["llm_status"] = "not_configured"
    if not configured():
        return data
    # Financial quantities, ISO dates and transaction identifiers remain in
    # Python widgets. The LLM sees only verified qualitative facts needed to
    # explain the situation, so it cannot accidentally echo monetary values or
    # invent a calendar date. Each request still reflects this user's financial
    # state, upcoming payments and changed events.
    next_bill = data["nearest_essential_payment"]
    facts = {
        "source": "synthetic_demo",
        "analysis_status": data["change_detection"]["status"],
        "change_event_codes": [event["code"] for event in data["change_detection"]["events"][:3]],
        "change_importance": [event["importance"] for event in data["change_detection"]["events"][:3]],
        "upcoming_essential_payment": (
            "none" if not next_bill else "not_covered" if not next_bill["covered"] else "covered"
        ),
        "future_minimum_balance": (
            "negative" if data["projected_minimum_balance"] < 0 else "non_negative"
        ),
        "additional_spending_capacity": (
            "none" if data["spending_capacity"]["estimated_additional_spend"] <= 0 else "positive"
        ),
        "has_saved_future_plan": data["saved_plans_count"] > 0,
        "future_expenses_are_predictions": True,
    }
    model = os.getenv("MOA_BRIEFING_MODEL") or os.getenv("MOA_AI_MODEL") or "gpt-4.1-mini"
    try:
        explanation = _explain(json.dumps(facts, sort_keys=True, ensure_ascii=False), model)
        # Append free-text rationale to the deterministic financial event summary.
        data["summary"] = data["summary"] + " " + explanation
        data["generation_mode"] = "llm_grounded_explanation"
        data["llm_used"] = True
        data["llm_model"] = model
        data["llm_status"] = "live"
    except AiUnavailable as exc:
        data["llm_status"] = exc.code
    return data

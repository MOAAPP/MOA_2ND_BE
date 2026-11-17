"""Grounded explanatory briefing; all financial numbers originate from Python."""
from __future__ import annotations
import json
import os
import re
from functools import lru_cache
from app.services.ai_provider import AiUnavailable, configured, structured_text, provider_settings
from app.services.ai_quality import FINANCIAL_AI_V10_POLICY, polite_sentences
from app.services.stage6 import briefing

_PROMPT = """너는 개인의 금융 상태를 검증된 정보만으로 설명하는 전문적이고 친절한 금융 교육 AI다.
입력은 실제 은행 기록이 아닌 합성 체험 데이터를 Python 엔진이 분석한 정성적 상태 신호다.
여기에 없는 금액·날짜·거래·원인을 절대로 만들거나 추측하지 마라.
모든 구체적인 금액과 날짜는 별도 Python UI가 표시한다.
설명에는 숫자(한글로 쓴 개수도 포함), 날짜, 금액, 수익 약속, 투자 권유, 소비 허가를 넣지 마라.
정성적 위험 신호가 있으면 결제와 잔액을 확인하도록 권하고, 없으면 위험이 있다고 단정하지 마라.
입력에 확정된 금융 변화가 없으면 변화가 발생했다고 단정하지 마라.
개발자 용어, 머신러닝 모델명, 코드, JSON 필드명을 본문에 나열하지 마라.
출력 예시: {"explanation":"다가오는 필수 결제를 고려해 지금의 소비 계획을 신중하게 살펴보세요."}
오직 {"explanation":"근거가 있는 정중한 한국어 설명 문장"} 형태의 JSON 객체만 출력하라.""" + "\n" + FINANCIAL_AI_V10_POLICY

def _verified_explanation(value: object) -> str | None:
    """Use only complete, grounded, polite sentences from an LLM explanation.

    FactChat's Claude response can be valid but longer than the 100-char UI
    allowance. Keep the first complete sentence; never cut mid-sentence.
    """
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value).strip()
    if not text or len(text) > 600:
        return None
    if re.search(r"[0-9０-９]|\d|[%₩$]", text) or not polite_sentences(text):
        return None
    if 10 <= len(text) <= 100:
        return text
    for sentence in re.findall(r"[^.!?。]+[.!?。]", text):
        sentence = sentence.strip()
        if 10 <= len(sentence) <= 100 and polite_sentences(sentence):
            return sentence
    return None


@lru_cache(maxsize=256)
def _explain(facts_json: str, model: str) -> str:
    from app.services import state_store
    import hashlib
    cache_key = 'ai-cache/briefing-v11-'+hashlib.sha256((model+facts_json).encode()).hexdigest()+'.json'
    if os.getenv('MOA_AI_CACHE') == 'on':
        cached = state_store.get(cache_key)
        valid_cached = _verified_explanation(cached.get('text'))
        if valid_cached:
            return valid_cached
    facts = json.loads(facts_json)
    result, _ = structured_text(_PROMPT, facts, model=model, max_tokens=200)
    explanation = _verified_explanation(result.get("explanation"))
    if not explanation:
        # One bounded retry for wrong/missing field, very short, or overly long
        # malformed prose. Never turn an invalid result into a fake LLM success.
        retry_prompt = (_PROMPT + "\n앞선 설명이 형식 검증에 실패했습니다. "
                        "반드시 explanation 필드에, 숫자 없이 완성된 존댓말 한 문장만 "
                        "30~70자로 작성하세요. JSON 객체만 출력하세요.")
        retry, _ = structured_text(retry_prompt, facts, model=model, max_tokens=180)
        explanation = _verified_explanation(retry.get("explanation"))
    if not explanation:
        raise AiUnavailable("invalid_grounded_explanation")
    if os.getenv('MOA_AI_CACHE') == 'on':
        state_store.put(cache_key, {'text': explanation, 'model': model})
    return explanation

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
    model = os.getenv("MOA_BRIEFING_MODEL") or provider_settings()[2]
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

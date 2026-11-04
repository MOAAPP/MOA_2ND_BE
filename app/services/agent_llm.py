"""Bounded, auditable OpenAI-compatible function calling for synthetic finance.

The model may parse intent and ask for whitelisted tools. It is never trusted
as a calculator, a source of account balances, or an optimizer.
"""
from __future__ import annotations
import json
import os
import re
from datetime import date
from time import perf_counter
from typing import Any

from app.schemas import CalculationRequest
from app.services.dataset import Dataset
from app.services.finance import calculate, state_for
from app.services.stage3 import compare_worlds
from app.services.agent_paths import GoalConditions, SearchRequest, search_paths, resolve_candidate
from app.stage3_models import WorldCompareRequest
from app.ml.forecast import run_forecast


PARSE_PARAMETERS: dict = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "purchase_required": {"type": "boolean"},
        "purchase_amount": {"type": ["integer", "null"]},
        "target_product_id": {"type": ["string", "null"]},
        "preferred_purchase_date": {"type": ["string", "null"], "description": "Exact ISO date only. No guesses."},
        "latest_purchase_date": {"type": ["string", "null"]},
        "minimum_balance": {"type": ["integer", "null"]},
        "required_savings_amount": {"type": ["integer", "null"]},
        "savings_goal_id": {"type": ["string", "null"]},
        "investment_allowed": {"type": "boolean"},
        "maximum_investment_amount": {"type": "integer"},
        "allowed_payment_methods": {"type": "array", "items": {"type": "string", "enum": ["debit", "transfer", "credit"]}},
        "hard_constraints": {"type": "array", "items": {"type": "string"}},
        "soft_preferences": {"type": "array", "items": {"type": "string"}},
        "confirmations_required": {"type": "array", "items": {"type": "string"}},
    },
}
PARSE_TOOL = {
    "type": "function", "function": {
        "name": "set_financial_conditions",
        "description": "Extract ONLY user-stated constraints. Never invent an amount, deadline, payment method or goal.",
        "parameters": PARSE_PARAMETERS,
    },
}
TOOL_SPEC = [
    ("get_financial_state", "Get the canonical account balance at the given date.", {
        "user_id": {"type": "string"}, "as_of": {"type": "string"}}),
    ("get_spending_forecast", "Get actual ML/baseline forecast provenance and future spending.", {
        "user_id": {"type": "string"}, "as_of": {"type": "string"}}),
    ("get_purchase_candidate", "Get the latest observed screenshot candidate and confirmed price.", {
        "user_id": {"type": "string"}, "as_of": {"type": "string"}}),
    ("simulate_cashflow", "Run canonical Python cashflow engine over 14 days.", {
        "user_id": {"type": "string"}, "as_of": {"type": "string"}}),
    ("simulate_savings", "Run the existing six-world calculator and return Python savings-world results.", {
        "user_id": {"type": "string"}, "as_of": {"type": "string"}, "amount": {"type": "integer"}}),
    ("simulate_investment", "Run the existing Python virtual-investment calculator.", {
        "user_id": {"type": "string"}, "as_of": {"type": "string"}, "amount": {"type": "integer"}}),
    ("validate_constraints", "Validate a proposed route against every hard constraint using Python.", {
        "user_id": {"type": "string"}, "as_of": {"type": "string"}, "purchase_amount": {"type": "integer"},
        "minimum_balance": {"type": "integer"}}),
    ("compare_scenarios", "Compare the six existing Python finance paths, no invented numbers.", {
        "user_id": {"type": "string"}, "as_of": {"type": "string"}, "purchase_amount": {"type": "integer"}}),
]
FINANCE_TOOLS = [{
    "type": "function",
    "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "additionalProperties": False,
                       "properties": fields, "required": ["user_id", "as_of"]},
    },
} for name, description, fields in TOOL_SPEC]
TOOL_NAMES = {name for name, _, _ in TOOL_SPEC}


class AgentFailure(Exception):
    pass


def _client():
    key = os.environ.get("MOA_AGENT_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise AgentFailure("ai_api_key_missing")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AgentFailure("openai_dependency_missing") from exc
    return OpenAI(api_key=key, base_url=os.environ.get("MOA_AGENT_BASE_URL") or None,
                  timeout=float(os.environ.get("MOA_AGENT_TIMEOUT_SECONDS", "12")),
                  max_retries=0)


def _usage(response: Any) -> dict:
    u = response.usage
    return {"input_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "completion_tokens", 0) or 0)}


def _numbers_in_text(prompt: str, amount: int) -> bool:
    # Numbers must come from user's text, not invented financial facts.
    digits = re.sub(r"[^0-9]", "", prompt)
    numeric_tokens = set(re.findall(r"\d[\d,]*", prompt))
    return str(amount) in {x.replace(",", "") for x in numeric_tokens} or (
        amount % 10000 == 0 and str(amount // 10000) in {x.replace(",", "") for x in numeric_tokens}
        and "만" in prompt
    )


def parse_conditions(client, model: str, goal_text: str, user_id: str, as_of: date):
    messages = [
        {"role": "system", "content": (
            "You extract Korean personal finance goals into tool parameters. Do not compute cash."
            " Hard constraints are explicitly mandatory, soft preferences are wishes."
            " Rent, card bill and minimum cash protection must not be relaxed."
            " If a date or amount is vague, return null and confirmations_required."
            " DO NOT invent a savings goal amount from existing data; no canonical savings-goal collection exists."
            " 'want to buy' is a preference, not purchase_required=true unless explicitly mandatory."
            " Date context as_of=" + as_of.isoformat()
        )},
        {"role": "user", "content": goal_text[:1200]},
    ]
    response = client.chat.completions.create(
        model=model, messages=messages, tools=[PARSE_TOOL],
        tool_choice={"type": "function", "function": {"name": "set_financial_conditions"}},
        temperature=0, max_tokens=450,
    )
    calls = response.choices[0].message.tool_calls or []
    if len(calls) != 1 or calls[0].function.name != "set_financial_conditions":
        raise AgentFailure("structured_conditions_tool_not_called")
    try:
        extracted = json.loads(calls[0].function.arguments)
        if not isinstance(extracted, dict):
            raise ValueError("expected object")
    except (ValueError, TypeError) as exc:
        raise AgentFailure("invalid_llm_conditions_json") from exc
    # Identity/date are ALWAYS server-controlled, never the model's choice.
    allowed = set(PARSE_PARAMETERS["properties"])
    extracted = {k: v for k, v in extracted.items() if k in allowed and v is not None}
    for key in ("purchase_amount", "minimum_balance", "required_savings_amount", "maximum_investment_amount"):
        amount = extracted.get(key)
        if amount and not _numbers_in_text(goal_text, amount):
            extracted.pop(key)
            if key == "maximum_investment_amount":
                extracted["maximum_investment_amount"] = 0
            extracted.setdefault("confirmations_required", []).append(
                f"{key}: 사용자가 제시한 금액인지 확인해 주세요.")
    # No LLM-constructed card settlement timing; explicit user confirmation required.
    if "credit" in extracted.get("allowed_payment_methods", []):
        extracted.setdefault("confirmations_required", []).append("신용카드 결제계좌 출금일 확인 필요")
    conditions = GoalConditions(user_id=user_id, as_of=as_of, **extracted)
    return conditions, _usage(response)


def execute_tool(ds: Dataset, name: str, payload: dict, uid: str, as_of: date) -> dict:
    if name not in TOOL_NAMES:
        raise AgentFailure("tool_not_allowed")
    if payload.get("user_id") != uid or payload.get("as_of") != as_of.isoformat():
        raise AgentFailure("cross_user_or_date_tool_call")
    def check_amount(key: str):
        x = payload.get(key, 0)
        if type(x) is not int or not (0 <= x <= 100_000_000):
            raise AgentFailure("invalid_tool_amount")
        return x
    if name == "get_financial_state":
        s = state_for(ds, CalculationRequest(user_id=uid, as_of=as_of))
        return {"account_id": s.account_id, "available_cash": s.available_cash}
    if name == "get_spending_forecast":
        f = run_forecast(ds, uid, as_of, 14)
        return {"model_type": f["model_type"], "data_quality_status": f["data_quality_status"],
                "forecast_run_id": f.get("forecast_run_id"), "forecasts": f.get("forecasts", [])}
    if name == "get_purchase_candidate":
        candidate, amount = resolve_candidate(ds, GoalConditions(user_id=uid, as_of=as_of))
        return {"candidate_id": (candidate or {}).get("id"), "purchase_amount": amount}
    if name == "simulate_cashflow":
        r = calculate(ds, CalculationRequest(user_id=uid, as_of=as_of, horizon_days=14))
        return {"available_cash": r.available_cash, "minimum_balance": r.minimum_balance,
                "essential_payments_covered": r.essential_payments_covered}
    amount = check_amount("amount" if name in ("simulate_savings", "simulate_investment") else "purchase_amount")
    minimum = check_amount("minimum_balance") if name == "validate_constraints" else 0
    r = compare_worlds(ds, WorldCompareRequest(
        user_id=uid, as_of=as_of, horizon_days=30,
        purchase_amount=amount, savings_amount=amount, investment_amount=amount,
        minimum_required_balance=minimum,
    ))
    if name == "simulate_savings":
        paths = [p for p in r.paths if p.scenario_id == "virtual_savings"]
    elif name == "simulate_investment":
        paths = [p for p in r.paths if p.scenario_id == "virtual_investment"]
    elif name == "validate_constraints":
        paths = [p for p in r.paths if p.scenario_id == "purchase_now"]
    else:
        paths = r.paths
    return {"paths": [{"scenario_id": p.scenario_id, "status": p.status,
                       "minimum_balance": p.minimum_balance,
                       "minimum_balance_met": p.minimum_balance_met,
                       "essential_payments_covered": p.essential_payments_covered}
                      for p in paths], "baseline_source": r.baseline_source}


def run_agent(ds: Dataset, uid: str, as_of: date, goal: str, overrides: dict | None = None,
              stress: str = "baseline", max_evaluations: int = 72,
              investment_return_percent: int = 0) -> dict:
    started = perf_counter()
    if uid not in ds.profiles:
        raise KeyError(uid)
    client = _client()
    model = os.environ.get("MOA_AGENT_MODEL", "gpt-4.1-mini")
    conditions, tokens = parse_conditions(client, model, goal, uid, as_of)
    if overrides:
        # User-edited structured controls supersede the model output.
        permitted = set(GoalConditions.model_fields) - {"user_id", "as_of"}
        changes = {k: v for k, v in overrides.items() if k in permitted}
        conditions = conditions.model_copy(update=changes)
        conditions = GoalConditions.model_validate(conditions.model_dump())
    audit = []
    messages = [
        {"role": "system", "content": (
            "You are a financial-tool planner, not a financial advisor or calculator."
            " Call the minimum useful registered tools to inspect the same synthetic user/date."
            " Never invent numbers. Do not use finance from another user. "
            " After tool calls, Python will enumerate and validate ALL recommended routes."
        )},
        {"role": "user", "content": json.dumps({
            "user_id": uid, "as_of": as_of.isoformat(), "goal": goal[:900],
            "conditions": conditions.model_dump(mode="json"),
        }, ensure_ascii=False)},
    ]
    duplicate_guard: set[str] = set()
    try:
        for round_no in range(3):
            response = client.chat.completions.create(
                model=model, messages=messages, tools=FINANCE_TOOLS,
                tool_choice="required" if round_no == 0 else "auto",
                temperature=0, max_tokens=500,
            )
            used = _usage(response)
            tokens = {k: tokens[k] + used[k] for k in tokens}
            msg = response.choices[0].message
            calls = msg.tool_calls or []
            if not calls:
                break
            messages.append(msg.model_dump(exclude_none=True))
            for call in calls[:4]:
                if len(audit) >= 8:
                    raise AgentFailure("tool_call_limit_exceeded")
                name = call.function.name
                try:
                    data = json.loads(call.function.arguments)
                    if not isinstance(data, dict):
                        raise ValueError("tool arguments must be object")
                    key = name + ":" + json.dumps(data, sort_keys=True, ensure_ascii=False)
                    if key in duplicate_guard:
                        raise AgentFailure("duplicate_tool_call")
                    duplicate_guard.add(key)
                    output = execute_tool(ds, name, data, uid, as_of)
                    audit.append({"tool": name, "status": "ok", "arguments": data})
                except Exception as exc:
                    audit.append({"tool": name, "status": "error", "error": str(exc)[:120]})
                    output = {"error": str(exc)[:120]}
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(output, ensure_ascii=False)[:2200]})
    except Exception as exc:
        # The validated Python optimizer is independent of planning tool availability.
        audit.append({"tool": "llm_tool_planning", "status": "error", "error": str(exc)[:120]})
    result = search_paths(ds, SearchRequest(conditions=conditions, stress=stress,
                                           max_evaluations=max_evaluations,
                                           investment_return_percent=investment_return_percent))
    inp = float(os.getenv("MOA_AGENT_INPUT_USD_PER_M", "0"))
    out = float(os.getenv("MOA_AGENT_OUTPUT_USD_PER_M", "0"))
    cost = round((tokens["input_tokens"] * inp + tokens["output_tokens"] * out) / 1_000_000, 8) if inp and out else None
    return {
        "status": result.status, "conditions": conditions.model_dump(mode="json"),
        "search": result.model_dump(mode="json"),
        "tool_execution_log": audit, "tool_success_rate": (
            sum(1 for a in audit if a["status"] == "ok") / len(audit) if audit else 0.0),
        "llm_model": model, "tokens": tokens, "estimated_cost_usd": cost,
        "cost_status": "configured_rates" if cost is not None else "pricing_not_configured",
        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        "numbers_source": "python_engine_and_ml_only",
    }

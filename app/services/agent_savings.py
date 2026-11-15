"""Explainable, bounded savings-goal alternatives powered by the existing cashflow engine.

This is deterministic goal-based decision support, not a bank product recommender.
No LLM guesses amounts and no 12-month cashflow forecast is implied.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.dataset import Dataset
from app.services.savings_plan import _add_months, plan_savings
from app.stage3_models import SavingsPlanRequest


class SavingsAdviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: SavingsPlanRequest


class SavingsAlternative(BaseModel):
    id: str
    title: str
    deposit_amount: int
    start_date: date
    payment_feasible_in_checked_window: bool
    reaches_goal_by_maturity_on_assumptions: bool
    projected_maturity_balance: int
    goal_progress_percent: float
    remaining_goal_amount: int
    goal_reached_date: date | None
    minimum_payment_account_balance: int
    first_shortfall_date: date | None
    checked_through: date
    explanation: list[str]


class SavingsAdviceResult(BaseModel):
    user_id: str
    as_of: date
    goal_amount: int
    status: Literal["recommended", "goal_already_met", "no_feasible_deposit", "base_cashflow_at_risk"]
    recommended_option_id: str | None
    alternatives: list[SavingsAlternative]
    explanation: str
    caveats: list[str] = Field(default_factory=lambda: [
        "가상 적금 계획이며 실제 금융상품 가입이나 자동이체가 아닙니다.",
        "현금흐름 검증은 최대 90일의 가정 기반 결과입니다. 만기까지의 자금 여유를 보장하지 않습니다.",
    ])


def _candidate_plans(request: SavingsPlanRequest, as_of: date):
    """At most six named options; don't pass a fake 'affordable' amount."""
    start = request.start_date or as_of + timedelta(days=1)
    amount = request.deposit_amount
    candidates = [
        ("requested", "요청한 납입액", request),
    ]
    if amount > 0:
        for fraction, ident, label in [
            (75, "three_quarters", "납입액 75%"),
            (50, "half", "납입액 50%"),
            (25, "quarter", "납입액 25%"),
        ]:
            smaller = amount * fraction // 100
            # A reduced plan must also reduce an explicitly supplied first installment.
            first = request.first_deposit_amount
            if first is not None:
                first = first * fraction // 100
            if smaller == 0:
                continue
            candidates.append((ident, label, request.model_copy(
                update={"deposit_amount": smaller, "first_deposit_amount": first})))
    # A zero-deposit baseline distinguishes an unaffordable plan from an
    # already risky payment account. Do not call it a savings recommendation.
    candidates.append(("no_new_deposit", "새 납입 없이 유지", request.model_copy(
        update={"deposit_amount": 0, "first_deposit_amount": 0})))
    if amount > 0:
        delayed = _add_months(start, 1)
        candidates.append(("delay_one_month", "한 달 뒤 납입 시작", request.model_copy(
            update={"start_date": delayed})))
    return candidates


def recommend_savings(ds: Dataset, request: SavingsAdviceRequest) -> SavingsAdviceResult:
    base = request.plan
    as_of = base.as_of or date.fromisoformat(ds.meta["snapshot_date"])
    options: list[SavingsAlternative] = []
    seen: set[tuple[int, int, date]] = set()
    for key, title, candidate in _candidate_plans(base, as_of):
        start = candidate.start_date or as_of + timedelta(days=1)
        signature = (candidate.deposit_amount, candidate.first_deposit_amount or 0, start)
        if signature in seen:
            continue
        seen.add(signature)
        outcome = plan_savings(ds, candidate)
        feasible = outcome.cashflow_validation_status == "satisfies_assumptions"
        reasons = []
        if not feasible:
            if outcome.first_shortfall_date:
                reasons.append(f"{outcome.first_shortfall_date.isoformat()}에 결제계좌 부족이 예상됩니다.")
            else:
                reasons.append("검증 기간에 최소 잔액 또는 필수결제 조건을 지키지 못합니다.")
        else:
            reasons.append(f"{outcome.cashflow_validated_through.isoformat()}까지의 계산에서 필수결제 및 최소 잔액 조건을 충족했습니다.")
        if outcome.goal_reached_by_maturity:
            reasons.append("납입 계획대로 진행하면 만기까지 설정한 목표 금액에 도달합니다.")
        else:
            reasons.append(f"계획상 만기에도 목표까지 {outcome.remaining_goal_amount:,}원이 부족합니다.")
        options.append(SavingsAlternative(
            id=key, title=title, deposit_amount=candidate.deposit_amount,
            start_date=outcome.start_date,
            payment_feasible_in_checked_window=feasible,
            reaches_goal_by_maturity_on_assumptions=outcome.goal_reached_by_maturity,
            projected_maturity_balance=outcome.projected_maturity_balance,
            goal_progress_percent=outcome.projected_goal_progress_percent,
            remaining_goal_amount=outcome.remaining_goal_amount,
            goal_reached_date=outcome.goal_reached_date,
            minimum_payment_account_balance=outcome.minimum_payment_account_balance,
            first_shortfall_date=outcome.first_shortfall_date,
            checked_through=outcome.cashflow_validated_through,
            explanation=reasons,
        ))

    original = next(x for x in options if x.id == "requested")
    baseline = next((x for x in options if x.id == "no_new_deposit"), None)
    if baseline is None and base.deposit_amount == 0 and (base.first_deposit_amount or 0) == 0:
        baseline = original
    if base.current_savings >= base.goal_amount:
        status = "goal_already_met"
        winner = baseline if baseline and baseline.payment_feasible_in_checked_window else None
        explanation = "이미 입력한 저축액으로 목표에 도달했습니다. 새 납입이 필요한지 먼저 확인하세요."
    elif baseline is not None and not baseline.payment_feasible_in_checked_window:
        status = "base_cashflow_at_risk"
        winner = None
        explanation = "새 적금을 넣지 않아도 결제계좌가 부족해질 수 있어요. 저축액을 추천하지 않고 지출·입금 일정을 먼저 확인해야 합니다."
    else:
        suitable = [x for x in options if x.id != "no_new_deposit"
                    and (x.deposit_amount > 0 or (base.first_deposit_amount or 0) > 0)
                    and x.payment_feasible_in_checked_window]
        if not suitable:
            status = "no_feasible_deposit"
            winner = None
            explanation = "검토한 납입 금액과 시작일 중 단기 현금흐름 조건을 만족하는 새 적금 계획이 없습니다."
        else:
            # Give priority to feasible plans reaching the goal, then smaller
            # shortfall and higher future savings. Never optimize on interest alone.
            suitable.sort(key=lambda x: (
                not x.reaches_goal_by_maturity_on_assumptions,
                x.remaining_goal_amount,
                -x.projected_maturity_balance,
                x.id != "requested",
            ))
            winner = suitable[0]
            status = "recommended"
            explanation = ("요청한 계획이 검증 기간에 조건을 충족합니다." if winner.id == original.id
                           else "요청한 납입 조건보다 현금흐름 제약에 맞는 대안을 찾았습니다.")
    return SavingsAdviceResult(
        user_id=base.user_id, as_of=as_of, goal_amount=base.goal_amount,
        status=status, recommended_option_id=winner.id if winner else None,
        alternatives=options, explanation=explanation,
    )

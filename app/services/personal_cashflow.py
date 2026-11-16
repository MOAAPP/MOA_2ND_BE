"""Personal-mode stateless rules using the SAME conservative day engine as S01.

Only numeric user-entered snapshot, recurring schedules and future dated entries
are sent to this calculator. No names, transactions before snapshot, AI calls,
storage, logging, fabricated market returns, or synthetic persona blending.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from collections import defaultdict

from app.personal_models import (
    PersonalSnapshot, PersonalCompareRequest, PersonalCompareResult, PersonalPath,
    PersonalDailyPoint, PersonalGoalRequest, PersonalGoalResult,
)
from app.schemas import CashEvent
from app.services.finance import eod, midday, simulate_days


def _clamp_month_day(day: date, due: int) -> bool:
    return day.day == min(due, monthrange(day.year, day.month)[1])


def _event(day: date, kind: str, signed: int, label: str, *, essential=False, idx=0):
    # Same CashEvent engine used for synthetic cases, but NO S01 user identity.
    return CashEvent(
        id=f"personal:{kind}:{day.isoformat()}:{idx}",
        user_id="anonymous", account_id="user_provided_account",
        debit_at=midday(day), occurred_at=None, amount=signed, label=label,
        kind=kind, payment_method="transfer",
        transaction_status="scheduled" if kind not in ("purchase", "virtual_allocation") else "simulated",
        transaction_source="user_entered_assumption",
        essential=essential,
    )


def _base_events(s: PersonalSnapshot, horizon: int) -> tuple[list[CashEvent], list[str]]:
    end = s.as_of + timedelta(days=horizon)
    upcoming = defaultdict(list)
    warnings = []
    for item in s.planned_transactions:
        if item.date > end:
            continue
        if item.payment_method in ("credit", "cash"):
            # Credit authorizations have NO known payment account debit day.
            # In particular NEVER bill them twice on top of the card schedule.
            warnings.append("credit_or_cash_transactions_without_confirmed_bank_debit_excluded")
            continue
        signed = item.amount if item.kind == "income" else -item.amount
        upcoming[item.date].append((signed, item))
    events: list[CashEvent] = []
    for i in range(1, horizon + 1):
        day = s.as_of + timedelta(days=i)
        listed = upcoming[day]
        has_salary = any(v > 0 and t.kind == "income" for v,t in listed)
        if s.monthly_income and _clamp_month_day(day, s.payday) and not has_salary:
            events.append(_event(day, "salary", s.monthly_income, "입력한 월급", idx=0))
        for j, bill in enumerate(s.bills):
            if _clamp_month_day(day, bill.day):
                events.append(_event(day, "fixed_bill", -bill.amount,
                                     "입력한 정기결제", essential=True, idx=j))
        for j, (signed, item) in enumerate(listed):
            events.append(_event(day, "planned", signed, "입력한 예정 거래", idx=j))
        if s.monthly_variable_budget:
            # A BUDGET is a user assumption, NOT an ML daily-spend forecast.
            variable = s.monthly_variable_budget // 30 + (
                1 if ((i - 1) % 30) < s.monthly_variable_budget % 30 else 0
            )
            if variable:
                events.append(_event(day, "variable_budget", -variable,
                                     "월 변동 지출 예산을 30일로 균등 분산", idx=0))
    if not s.monthly_variable_budget:
        warnings.append("variable_future_spending_not_provided_zero_assumed")
    else:
        warnings.append("variable_future_spending_is_user_budget_not_ml_forecast")
    if not s.bills:
        warnings.append("no_registered_fixed_bills")
    if not s.monthly_income:
        warnings.append("no_registered_salary")
    if any(x.kind == "expense" for x in s.planned_transactions) and s.bills:
        warnings.append("planned_expenses_may_duplicate_monthly_fixed_bills_verify_entries")
    return events, sorted(set(warnings))


def _path(s: PersonalSnapshot, horizon: int, base: list[CashEvent],
          sid: str, amount: int, when: date | None, *,
          status: str = "calculated") -> PersonalPath:
    events = list(base)
    if when is not None and when <= s.as_of + timedelta(days=horizon):
        events.append(_event(when, "purchase" if sid in ("purchase_now", "purchase_delay") else
                             "virtual_allocation", -amount,
                             "가상 선택 지출", idx=0))
    # The engine conservatively applies all daily debits before unknown-time
    # incoming salary; don't credit a salary early to justify same-day buys.
    days, essentials = simulate_days(eod(s.as_of), s.balance, events, horizon,
                                    s.minimum_balance)
    low = min([s.balance] + [d.lowest_balance for d in days])
    first = s.as_of if s.balance < s.minimum_balance else next(
        (d.date for d in days if d.lowest_balance < s.minimum_balance), None)
    valid = low >= s.minimum_balance
    essential_ok = all(payment["covered"] for payment in essentials)
    return PersonalPath(
        scenario_id=sid, status=status, available_cash=s.balance,
        ending_balance=days[-1].closing_balance, minimum_balance=low,
        minimum_balance_met=valid, essential_payments_covered=essential_ok,
        first_shortfall_date=first,
        daily_cashflow=[PersonalDailyPoint(date=d.date,
                         closing_balance=d.closing_balance,
                         lowest_balance=d.lowest_balance) for d in days],
    )


def compare_personal(req: PersonalCompareRequest) -> PersonalCompareResult:
    s = req.snapshot
    amount, horizon = req.purchase_amount, req.horizon_days
    base, warnings = _base_events(s, horizon)
    tomorrow = s.as_of + timedelta(days=1)
    delay = tomorrow + timedelta(days=req.purchase_delay_days - 1)
    # Cash-only model: investing/savings takes money out immediately but does
    # NOT pretend it is spendable cash or calculate market appreciation.
    specs = [
        ("purchase_now", amount, tomorrow, "calculated"),
        ("no_purchase", 0, None, "calculated"),
        ("purchase_delay", amount, delay,
         "calculated" if req.purchase_delay_days <= horizon else "out_of_horizon"),
        ("virtual_savings", amount, tomorrow, "calculated"),
        ("virtual_investment", amount, tomorrow, "calculated"),
        ("mixed", amount, tomorrow, "calculated"),
    ]
    paths = [_path(s, horizon, base, sid, value, when, status=status)
             for sid, value, when, status in specs]
    return PersonalCompareResult(
        as_of=s.as_of, available_cash=s.balance,
        minimum_required_balance=s.minimum_balance,
        purchase_amount=amount, horizon_days=horizon,
        payment_method=req.payment_method,
        forecast_source=("user_budget_assumption_not_ml" if s.monthly_variable_budget
                         else "no_variable_spend_assumption"),
        warnings=warnings + [
            "hypothetical_results_not_real_bank_ledger",
            "all_virtual_savings_and_investments_are_cash_outflows_not_guaranteed_returns",
            "future_incomes_and_spending_require_user_confirmation",
        ],
        paths=paths,
    )


def analyze_goal(req: PersonalGoalRequest) -> PersonalGoalResult:
    # Deterministic constrained path screening. This is NOT an LLM/AI claim.
    comparison = compare_personal(PersonalCompareRequest(
        snapshot=req.snapshot, purchase_amount=req.target_amount,
        horizon_days=req.horizon_days,
        purchase_delay_days=min(30, req.horizon_days)))
    allowed = {
        "purchase": ("purchase_now", "purchase_delay"),
        "savings": ("virtual_savings",),
        "investment": ("virtual_investment",),
    }[req.goal_type]
    valid = []
    reasons = defaultdict(int)
    for p in comparison.paths:
        if p.scenario_id not in allowed:
            continue
        if req.goal_type == "investment" and not req.investment_allowed:
            reasons["investment_permission"] += 1
        elif p.minimum_balance_met and p.essential_payments_covered:
            valid.append(p)
        elif not p.minimum_balance_met:
            reasons["minimum_balance"] += 1
        else:
            reasons["essential_bills"] += 1
    confirmations = [
        "목표 기간 전체의 추가 지출과 수입을 확인해 주세요.",
        "이 분석은 등록한 값만 반영한 규칙 기반 가상 계산이며 AI 모델이나 은행 잔고 조회가 아닙니다.",
    ]
    if req.goal_type == "savings":
        confirmations.append("저축은 목표액을 한 번에 옮기는 가정입니다. 월별 적금 납입 계획은 별도 검증이 필요합니다.")
    return PersonalGoalResult(
        as_of=req.snapshot.as_of, target_amount=req.target_amount,
        goal_type=req.goal_type, horizon_days=req.horizon_days,
        evaluated_count=len(allowed), valid_count=len(valid),
        routes=valid, rejection_counts=dict(reasons),
        confirmations_required=confirmations, warnings=comparison.warnings,
    )

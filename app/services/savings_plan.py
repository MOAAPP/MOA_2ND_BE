"""User-defined savings plans with an independently bounded cashflow preview."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.services.dataset import Dataset
from app.services.finance import FinanceProblem
from app.services.stage3 import _at, compare_worlds
from app.stage3_models import (
    SavingsPlanPoint, SavingsPlanRequest, SavingsPlanResult,
    ScenarioEvent, WorldCompareRequest,
)


def _add_months(anchor: date, months: int) -> date:
    index = anchor.year * 12 + anchor.month - 1 + months
    year, month_index = divmod(index, 12)
    month = month_index + 1
    return date(year, month, min(anchor.day, monthrange(year, month)[1]))


def _deposits(req: SavingsPlanRequest, start: date, maturity: date) -> list[tuple[date, int]]:
    schedule = []
    index = 0
    while True:
        if req.deposit_frequency == "monthly":
            when = _add_months(start, index)
        else:
            days = 7 if req.deposit_frequency == "weekly" else 14
            when = start + timedelta(days=index * days)
        if when >= maturity:
            break
        amount = (req.first_deposit_amount if req.first_deposit_amount is not None
                  else req.deposit_amount) if index == 0 else req.deposit_amount
        if amount:
            schedule.append((when, amount))
        index += 1
    return schedule


def savings_projection(req: SavingsPlanRequest, as_of: date):
    """Pure contribution projection. Interest is credited only at maturity."""
    start = req.start_date or as_of + timedelta(days=1)
    if start <= as_of:
        raise FinanceProblem("savings_start_date_must_follow_observation_date")
    maturity = _add_months(start, req.duration_months)
    deposits = _deposits(req, start, maturity)
    balance = req.current_savings
    reached = as_of if balance >= req.goal_amount else None

    def pct(value: int) -> float:
        return round(min(100, Decimal(value) * 100 / Decimal(req.goal_amount)), 2)

    timeline = [SavingsPlanPoint(
        date=as_of, deposit_amount=0, principal_balance=balance,
        interest_credited=0, projected_balance=balance, goal_progress_percent=pct(balance),
    )]
    for when, amount in deposits:
        balance += amount
        if reached is None and balance >= req.goal_amount:
            reached = when
        timeline.append(SavingsPlanPoint(
            date=when, deposit_amount=amount, principal_balance=balance,
            interest_credited=0, projected_balance=balance, goal_progress_percent=pct(balance),
        ))

    interest = None
    if req.annual_rate_percent is not None:
        rate = req.annual_rate_percent / Decimal(100)
        accrued = sum((Decimal(amount) * rate * Decimal((maturity - when).days) / Decimal(365)
                       for when, amount in deposits), Decimal(0))
        interest = int(accrued.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    final_balance = balance + (interest or 0)
    if reached is None and final_balance >= req.goal_amount:
        reached = maturity
    timeline.append(SavingsPlanPoint(
        date=maturity, deposit_amount=0, principal_balance=balance,
        interest_credited=interest or 0, projected_balance=final_balance,
        goal_progress_percent=pct(final_balance),
    ))
    return start, maturity, deposits, timeline, interest, reached, final_balance


def plan_savings(ds: Dataset, req: SavingsPlanRequest) -> SavingsPlanResult:
    if req.user_id not in ds.profiles:
        raise KeyError(req.user_id)
    begin = date.fromisoformat(ds.meta["start_date"])
    end = date.fromisoformat(ds.meta["snapshot_date"])
    as_of = req.as_of or end
    if not begin <= as_of <= end:
        raise FinanceProblem("observation_date_out_of_range")

    start, maturity, deposits, timeline, interest, reached, ending = savings_projection(req, as_of)
    # The canonical engine supports up to 90 days. Never describe the remaining
    # months as a verified or ML-predicted payment-account balance.
    window = min(90, max(14, (maturity - as_of).days))
    validated_through = as_of + timedelta(days=window)
    within = [(when, amount) for when, amount in deposits if when <= validated_through]
    events = [ScenarioEvent(
        event_id=f"savings-plan:{i}:{when.isoformat()}",
        scenario_id="virtual_savings", user_id=req.user_id,
        event_type="savings_deposit", effective_at=when, debit_at=_at(when),
        payment_method="transfer", amount=amount,
        metadata={"name": "계획한 가상 적금 납입", "plan_index": i},
    ) for i, (when, amount) in enumerate(within, start=1)]
    worlds = compare_worlds(ds, WorldCompareRequest(
        user_id=req.user_id, as_of=as_of, horizon_days=window,
        purchase_amount=0, savings_amount=0,
        minimum_required_balance=req.minimum_required_balance,
        stress=req.stress,
        forecast_daily_spend_override=req.forecast_daily_spend_override,
        custom_events=events,
    ))
    savings = next(path for path in worlds.paths if path.scenario_id == "virtual_savings")
    if savings.virtual_savings != sum(amount for _, amount in within):
        raise FinanceProblem("savings_plan_cashflow_mismatch")
    valid = (savings.status == "calculated" and savings.minimum_balance_met
             and savings.essential_payments_covered)
    warnings = ["hypothetical_plan_no_real_transfer", "future_deposits_assumed_not_confirmed"]
    if interest is None:
        warnings.append("interest_not_estimated_without_explicit_rate")
    else:
        warnings.append("simple_interest_actual_365_before_tax_at_maturity_not_product_quote")
    if any(when > validated_through for when, _ in deposits):
        warnings.append("deposits_after_cashflow_preview_not_financially_validated")
    if maturity > validated_through:
        warnings.append("maturity_balance_is_contribution_projection_not_long_term_cashflow_forecast")
    if not valid:
        warnings.append("short_term_cashflow_or_essential_payments_at_risk")
    return SavingsPlanResult(
        user_id=req.user_id, as_of=as_of, dataset_id=ds.meta["dataset_id"],
        goal_amount=req.goal_amount, current_savings=req.current_savings,
        start_date=start, maturity_date=maturity,
        deposit_frequency=req.deposit_frequency, duration_months=req.duration_months,
        planned_deposit_count=len(deposits),
        planned_deposits_total=sum(amount for _, amount in deposits),
        projected_principal=req.current_savings + sum(amount for _, amount in deposits),
        estimated_interest_before_tax=interest,
        projected_maturity_balance=ending,
        projected_goal_progress_percent=timeline[-1].goal_progress_percent,
        remaining_goal_amount=max(0, req.goal_amount - ending),
        goal_reached_date=reached, goal_reached_by_maturity=reached is not None,
        timeline=timeline,
        cashflow_validation_days=window, cashflow_validated_through=validated_through,
        cashflow_validation_status="satisfies_assumptions" if valid else "fails_assumptions",
        minimum_payment_account_balance=savings.minimum_balance,
        minimum_required_balance_met=savings.minimum_balance_met,
        essential_payments_covered=savings.essential_payments_covered,
        first_shortfall_date=savings.first_shortfall_date,
        deposits_within_validation=sum(amount for _, amount in within),
        deposits_beyond_validation=sum(amount for when, amount in deposits if when > validated_through),
        cashflow_preview=savings.daily_cashflow, warnings=warnings,
    )

"""Stage 3 deterministic overlay: one financial engine, six independent ledgers.

Baseline variable spend is an explicitly named historical mean, not an ML prediction.
The dataset's mock portfolio is never silently credited to the payment account.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import date, datetime, timedelta, time
from decimal import Decimal
from uuid import uuid4

from app.schemas import CalculationRequest, ExternalEvent
from app.services.dataset import Dataset
from app.services.finance import KST, FinanceProblem, calculate
from app.stage3_models import (
    WorldCompareRequest, WorldCompareResult, ScenarioPath,
    ScenarioEvent, ScenarioDay, Allocation,
)

LABELS = {
    "purchase_now": "지금 구매", "no_purchase": "미구매",
    "purchase_delay": "구매 연기", "virtual_savings": "가상 저축",
    "virtual_investment": "가상 투자", "mixed": "혼합 배분",
}


def _at(day: date) -> datetime:
    return datetime.combine(day, time(12, 0), KST)


def _add_month(day: date, n: int) -> date:
    from calendar import monthrange
    idx = (day.year * 12 + day.month - 1) + n
    year, month0 = divmod(idx, 12)
    return date(year, month0 + 1, min(day.day, monthrange(year, month0 + 1)[1]))


def _observed_spend(u, as_of: date, override: int | None):
    if override is not None:
        return override, "explicit_daily_spend_assumption"
    rows = sorted((d for d in u.daily
                   if d.get("date", "") <= as_of.isoformat()
                   and d.get("observation_complete")
                   and not d.get("future_scenario")
                   and isinstance(d.get("observed_variable_spend"), int)),
                  key=lambda d: d["date"])
    if not rows:
        raise FinanceProblem("no_observed_variable_spending_for_baseline")
    recent = rows[-7:]
    return (sum(int(d["observed_variable_spend"]) for d in recent) // len(recent),
            f"observed_{len(recent)}_day_mean_not_ml")


def _mock_price(u, code: str, as_of: date):
    rows = [r for r in u.investments if r.get("code") == code
            and r.get("date", "") <= as_of.isoformat()
            and not r.get("future_scenario")
            and r.get("price", 0) > 0]
    return int(max(rows, key=lambda x: (x["date"], x.get("at", 0)))["price"]) if rows else None


def _event(sid, uid, kind, day, amount, account, method="transfer",
           debit_day=None, **metadata):
    return ScenarioEvent(
        event_id=f"{sid}:{kind}:{day}:{len(metadata)}:{amount}",
        scenario_id=sid, user_id=uid, event_type=kind,
        effective_at=day, debit_at=_at(debit_day or day),
        account_id=account, payment_method=method, amount=amount,
        metadata=metadata,
    )


def _cash_event(item, amount):
    return ExternalEvent(
        id=item.event_id, user_id=item.user_id, account_id=item.account_id,
        debit_at=item.debit_at or _at(item.effective_at), amount=amount,
        kind=item.event_type, label=item.metadata.get("name", item.event_type),
        essential=False, payment_method=item.payment_method,
        status="simulated", source="simulation",
    )


def compare_worlds(ds: Dataset, req: WorldCompareRequest) -> WorldCompareResult:
    u = ds.get_user(req.user_id)
    cutoff = date.fromisoformat(ds.meta["snapshot_date"])
    start = date.fromisoformat(ds.meta["start_date"])
    day = req.as_of or cutoff
    if day < start or day > cutoff:
        raise FinanceProblem(f"as_of_outside_observed_range:{start}..{cutoff}")
    account = u.profile["payment_account_id"]
    base = calculate(ds, CalculationRequest(user_id=u.user_id, account_id=account,
                                            as_of=day, horizon_days=req.horizon_days,
                                            minimum_required_balance=req.minimum_required_balance))
    spend, spend_origin = _observed_spend(u, day, req.forecast_daily_spend_override)
    instrument = req.investment_code or next(
        (r["code"] for r in reversed(u.investments) if r.get("date", "") <= day.isoformat()
         and not r.get("future_scenario")), None)
    unit_price = req.investment_unit_price or (_mock_price(u, instrument, day) if instrument else None)
    invest_origin = "explicit_unit_price" if req.investment_unit_price else "historical_fabricated_test_price"
    proposed_day = req.purchase_date or day + timedelta(days=1)
    saved_day = req.savings_date or proposed_day
    if proposed_day <= day or saved_day <= day:
        raise FinanceProblem("scenario_choice_date_must_follow_as_of")
    if req.allocation is not None and req.allocation.total > base.available_cash:
        raise FinanceProblem("allocation_exceeds_available_payment_cash")
    amount = req.purchase_amount
    mixed = req.allocation or Allocation(
        purchase=amount * 40 // 100, savings=amount * 30 // 100,
        investment=amount * 20 // 100, cash=amount - (amount * 90 // 100))
    if mixed.total > amount or mixed.total > base.available_cash:
        # Still report other five paths; mixed is not a valid affordable alternative.
        mixed_problem = "mixed_allocation_exceeds_available_cash"
    else:
        mixed_problem = None

    horizon_end = day + timedelta(days=req.horizon_days)
    high_spend = req.stress in ("higher_spending", "combined")
    delay_income = req.income_delay_days if req.stress in ("delayed_income", "combined") else 0
    daily_spend = spend + (round(spend * req.daily_spend_increase_percent / 100) if high_spend else 0)
    # Forecast input: explicit, deterministic non-ML baseline; same in all six paths.
    forecast = [
        ExternalEvent(
            id=f"baseline:variable:{d.isoformat()}", user_id=u.user_id,
            account_id=account, debit_at=_at(d), amount=-daily_spend,
            kind="variable_expense_baseline", label="관측 지출의 일평균 기준선",
            payment_method="debit", status="predicted", source="rules",
            forecast_run_id=f"rules:{day.isoformat()}:{spend_origin}",
        )
        for i in range(1, req.horizon_days + 1)
        for d in [day + timedelta(days=i)]
    ]

    worlds = []
    for sid in LABELS:
        events: list[ScenarioEvent] = []
        notes = [spend_origin, "baseline_is_not_trained_ml"]
        if req.allocation is None and sid == "mixed":
            notes.append("editable_40_30_20_10_allocation_assumption_not_user_record")
        needs_confirmation = False
        invalid = sid == "mixed" and mixed_problem is not None
        purchase_amount = amount if sid in ("purchase_now", "purchase_delay") else mixed.purchase if sid == "mixed" else 0
        purchase_day = proposed_day + timedelta(days=req.purchase_delay_days if sid == "purchase_delay" else 0)
        if sid == "no_purchase":
            events.append(_event(sid, u.user_id, "no_purchase", proposed_day, 0, account))
        if purchase_amount:
            credit = req.payment_method == "credit"
            # For delayed credit purchases a NEW settlement date must be confirmed.
            debit_day = req.delayed_credit_debit_date if sid == "purchase_delay" else req.credit_debit_date
            if credit and (debit_day is None or debit_day < purchase_day):
                needs_confirmation = True
                notes.append("credit_settlement_date_requires_confirmation")
            else:
                events.append(_event(sid, u.user_id,
                                     "purchase_delay" if sid == "purchase_delay" else "purchase",
                                     purchase_day, purchase_amount, account, req.payment_method,
                                     debit_day=debit_day, name="선택한 상품 구매"))
        save_amount = (req.savings_amount if req.savings_amount is not None else amount) if sid == "virtual_savings" else mixed.savings if sid == "mixed" else 0
        investment_budget = (req.investment_amount if req.investment_amount is not None else amount) if sid == "virtual_investment" else mixed.investment if sid == "mixed" else 0
        if save_amount:
            events.append(_event(sid, u.user_id, "savings_deposit", saved_day, save_amount,
                                 account, name="가상 저축 입금"))
        if save_amount and req.savings_monthly_amount:
            for month in range(1, 5):
                installment_day = _add_month(saved_day, month)
                if installment_day > horizon_end:
                    break
                events.append(_event(sid, u.user_id, "savings_deposit",
                                     installment_day, req.savings_monthly_amount,
                                     account, name="정기 가상 저축", installment=month))
        if investment_budget and not unit_price:
            needs_confirmation = True
            notes.append("investment_reference_price_missing")
        elif investment_budget and unit_price:
            qty = investment_budget // unit_price
            if qty:
                events.append(_event(sid, u.user_id, "virtual_investment_buy", proposed_day,
                                     qty * unit_price, account, "transfer", code=instrument or "CUSTOM",
                                     qty=qty, unit_price=unit_price, price_source=invest_origin))
            else:
                notes.append("investment_budget_below_one_share_price_cash_retained")
        if sid == "virtual_investment" and req.virtual_sell_qty:
            if req.virtual_sell_unit_price is None:
                needs_confirmation = True
                notes.append("virtual_sell_unit_price_requires_confirmation")
            else:
                sell_day = req.virtual_sell_date or proposed_day + timedelta(days=1)
                settle_day = req.virtual_sell_settlement_date or sell_day
                if sell_day < proposed_day or settle_day < sell_day:
                    raise FinanceProblem("virtual_sell_date_or_settlement_before_buy")
                if req.virtual_sell_settlement_date is None:
                    notes.append("virtual_sell_same_day_settlement_is_explicit_sandbox_assumption")
                events.append(_event(
                    sid, u.user_id, "virtual_investment_sell", sell_day,
                    req.virtual_sell_qty * req.virtual_sell_unit_price,
                    account, "transfer", debit_day=settle_day,
                    name="가상 주식·ETF 매도 정산", code=instrument or "CUSTOM",
                    qty=req.virtual_sell_qty, unit_price=req.virtual_sell_unit_price,
                    price_source="user_assumption",
                ))
        # Explicit events apply only to their matching path, never to another user's data.
        events.extend(e.model_copy(update={"account_id": account}) for e in req.custom_events
                      if e.scenario_id == sid and e.user_id == u.user_id)
        if any(e.user_id != u.user_id for e in req.custom_events):
            raise FinanceProblem("cross_user_scenario_event")
        ids = [e.event_id for e in events]
        if len(ids) != len(set(ids)):
            raise FinanceProblem("duplicate_scenario_event_id")
        cash_items = []
        scenario_positions = defaultdict(lambda: {"qty": 0, "cost": 0})
        saving_balance = 0
        valuation_override = None
        balance_events = defaultdict(list)
        for e in sorted(events, key=lambda x: (x.debit_at or _at(x.effective_at), x.event_id)):
            if e.effective_at <= day:
                raise FinanceProblem("scenario_event_before_or_on_observation")
            balance_events[e.effective_at].append(e)
            if e.event_type in ("purchase", "purchase_delay", "savings_deposit", "virtual_investment_buy"):
                cash_items.append(_cash_event(e, -e.amount))
            elif e.event_type == "virtual_investment_sell":
                cash_items.append(_cash_event(e, e.amount))
            elif e.event_type == "adjustable_expense_change":
                sign = 1 if e.metadata.get("direction") == "decrease" else -1
                cash_items.append(_cash_event(e, sign * e.amount))
            elif e.event_type == "savings_adjustment":
                cash_items.append(_cash_event(e, -e.amount if e.metadata.get("direction") != "withdraw" else e.amount))
            elif e.event_type == "income_delay":
                days_late = e.metadata.get("delay_days")
                if type(days_late) is not int or not (0 <= days_late <= 31):
                    raise FinanceProblem("income_delay_event_requires_integer_delay_days_0_31")
                notes.append(f"custom_income_delay_{days_late}_days")
        path_income_delay = delay_income
        for event in events:
            if event.event_type == "income_delay":
                path_income_delay = int(event.metadata["delay_days"])
        try:
            result = calculate(ds, CalculationRequest(
                user_id=u.user_id, account_id=account, as_of=day,
                horizon_days=req.horizon_days, minimum_required_balance=req.minimum_required_balance,
                income_delay_days=path_income_delay, forecast_events=forecast,
                simulation_events=[] if needs_confirmation or invalid else cash_items,
            ))
        except ValueError as ex:
            raise FinanceProblem(str(ex)) from ex
        days = []
        cash_savings = 0
        held_qty = 0
        cost = 0
        invested_value = 0
        realized_gain = 0
        goal_date = None
        for item in result.daily_cashflow:
            current_day = item.date
            for ev in balance_events[current_day] if not (needs_confirmation or invalid) else []:
                if ev.event_type in ("savings_deposit", "savings_adjustment"):
                    direction = -1 if ev.metadata.get("direction") == "withdraw" else 1
                    cash_savings += ev.amount * direction
                    if cash_savings < 0:
                        raise FinanceProblem("savings_withdrawal_exceeds_savings")
                elif ev.event_type == "virtual_investment_buy":
                    q = int(ev.metadata.get("qty", 0))
                    px = int(ev.metadata.get("unit_price", 0))
                    if q <= 0 or px <= 0 or q * px != ev.amount:
                        raise FinanceProblem("investment_buy_value_mismatch")
                    held_qty += q
                    cost += ev.amount
                elif ev.event_type == "virtual_investment_sell":
                    q = int(ev.metadata.get("qty", 0))
                    if q <= 0 or q > held_qty:
                        raise FinanceProblem("investment_sell_exceeds_funded_holdings")
                    sell_price = int(ev.metadata.get("unit_price", 0))
                    if sell_price <= 0 or q * sell_price != ev.amount:
                        raise FinanceProblem("investment_sell_value_mismatch")
                    released_cost = round(cost * q / held_qty)
                    realized_gain += ev.amount - released_cost
                    cost -= released_cost
                    held_qty -= q
                elif ev.event_type == "virtual_asset_revaluation":
                    valuation_override = int(ev.metadata.get("unit_price", unit_price or 0))
            price_today = valuation_override or unit_price or 0
            if req.stress == "investment_loss":
                # Applied only when explicitly specified; 0% remains unchanged.
                price_today = max(0, round(price_today * (1 + min(0, req.investment_return_percent) / 100)))
            elif req.investment_return_percent:
                price_today = max(0, round(price_today * (1 + req.investment_return_percent / 100)))
            invested_value = held_qty * price_today
            if req.savings_goal_target is not None and goal_date is None and (
                req.savings_goal_current + cash_savings >= req.savings_goal_target):
                goal_date = current_day
            days.append(ScenarioDay(
                date=current_day, opening_balance=item.opening_balance, inflow=item.inflow,
                outflow=item.outflow, closing_balance=item.closing_balance,
                lowest_balance=item.lowest_balance, savings_balance=cash_savings,
                investment_value=invested_value, investment_profit_loss=invested_value-cost+realized_gain))
        if req.savings_goal_target is not None and req.savings_goal_current >= req.savings_goal_target:
            goal_date = day
        balances = {key: value for key, value in result.future_balances.items()}
        short = next((item.date for item in days if item.lowest_balance < req.minimum_required_balance), None)
        minimum = result.minimum_balance
        status = "invalid" if invalid else "needs_confirmation" if needs_confirmation else "calculated"
        if invalid:
            notes.append(mixed_problem or "invalid_allocation")
        if needs_confirmation:
            notes.append("no_scenario_debit_applied_until_required_fields_confirmed")
        if req.savings_annual_rate is not None:
            notes.append("interest_not_accrued_before_defined_product_compounding_terms")
        worlds.append(ScenarioPath(
            scenario_id=sid, label=LABELS[sid], status=status, events=events,
            available_cash=result.available_cash, future_balances=balances,
            daily_cashflow=days, minimum_balance=minimum,
            first_shortfall_date=short, minimum_balance_met=minimum >= req.minimum_required_balance,
            essential_payments_covered=result.essential_payments_covered is True,
            virtual_savings=cash_savings, virtual_investment_value=invested_value,
            virtual_investment_profit_loss=invested_value-cost+realized_gain,
            virtual_investment_realized_profit_loss=realized_gain,
            investment_quantity=held_qty,
            investment_average_price=round(cost/held_qty, 2) if held_qty else None,
            savings_goal_reached_date=goal_date,
            purchase_completed=bool(purchase_amount and not needs_confirmation and not invalid),
            purchase_delay_days=req.purchase_delay_days if sid == "purchase_delay" else 0,
            assumptions=notes,
        ))
    return WorldCompareResult(
        dataset_id=ds.meta["dataset_id"], user_id=u.user_id, as_of=day,
        observation_start=start, observation_end=cutoff, baseline_source=spend_origin,
        baseline_daily_variable_expense=spend, account_id=account,
        available_cash=base.available_cash, reference_investment_price=unit_price,
        reference_investment_code=instrument, paths=worlds,
        warnings=["synthetic_dataset_no_real_bank_connection",
                  "historical_mock_portfolio_not_liquid_payment_cash",
                  "not_trained_ml_prediction"],
    )

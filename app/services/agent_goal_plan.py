"""Bounded integrated decision search for purchases, recurring savings and stocks/ETFs.

Each candidate has ONE canonical financial simulation containing every cash debit
and settled sale receipt. Never 'add together' separately safe scenarios.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from itertools import product

from app.goal_plan_models import (
    IntegratedGoalChartPoint, IntegratedGoalRequest, IntegratedGoalResult,
    IntegratedGoalRoute,
)
from app.portfolio_models import PortfolioSimulationRequest
from app.schemas import CalculationRequest, ExternalEvent
from app.services.agent_savings import _candidate_plans
from app.services.dataset import Dataset
from app.services.finance import FinanceProblem, calculate, midday
from app.services.portfolio import _forecast, _ledger, _validate_and_prepare
from app.services.savings_plan import savings_projection


def _event(user: str, account: str, key: str, when: date, amount: int, kind: str):
    return ExternalEvent(
        id=f"agent-goal:{user}:{key}", user_id=user, account_id=account,
        debit_at=midday(when), amount=amount, kind=kind,
        label="가상 소비·저축 조건 비교", payment_method="transfer",
        status="simulated", source="simulation",
    )


def _purchase_options(req: IntegratedGoalRequest, as_of: date, limit: date):
    if not req.purchase_amount:
        return [("no_purchase", None)]
    when = req.purchase_date or as_of + timedelta(days=1)
    if not (as_of < when <= limit):
        raise FinanceProblem("integrated_purchase_date_outside_validation_window")
    choices = [("purchase_now", when)]
    delayed = when + timedelta(days=req.purchase_delay_days)
    if delayed <= limit:
        choices.append(("purchase_delay", delayed))
    if not req.purchase_required:
        choices.append(("no_purchase", None))
    return choices


def _sort_key(route: IntegratedGoalRoute, goal_already_met: bool):
    return (
        not route.savings_goal_reached_by_maturity_on_assumptions,
        route.remaining_savings_goal_amount,
        bool(goal_already_met and route.savings_deposit_amount),
        route.purchase_strategy == "no_purchase",
        route.purchase_strategy == "purchase_delay",
        route.investment_included,  # Never assume hypothetical investment gain is guaranteed.
        -route.payment_account_minimum_balance,
        route.id,
    )


def integrated_goal_plan(ds: Dataset, req: IntegratedGoalRequest) -> IntegratedGoalResult:
    if req.user_id not in ds.profiles:
        raise KeyError(req.user_id)
    begin = date.fromisoformat(ds.meta["start_date"])
    cutoff = date.fromisoformat(ds.meta.get("canonical_snapshot_date", ds.meta["snapshot_date"]))
    as_of = req.as_of or req.savings_plan.as_of or cutoff
    if not begin <= as_of <= cutoff:
        raise FinanceProblem("integrated_goal_observation_date_out_of_range")
    limit = as_of + timedelta(days=req.horizon_days)
    savings = req.savings_plan.model_copy(update={"as_of": as_of})
    account = ds.get_user(req.user_id).profile["payment_account_id"]
    forecast, source = _forecast(ds, req.user_id, as_of, req.horizon_days,
                                savings.forecast_daily_spend_override)
    # The existing forecast helper is baseline-only. Stress assumptions are not
    # silently dropped: reject until there is a shared stress transform.
    if savings.stress != "baseline":
        raise FinanceProblem("integrated_goal_only_baseline_stress_supported")
    shared = dict(
        user_id=req.user_id, account_id=account, as_of=as_of,
        horizon_days=req.horizon_days,
        minimum_required_balance=savings.minimum_required_balance,
        forecast_events=forecast,
    )
    baseline = calculate(ds, CalculationRequest(**shared))
    baseline_ok = (baseline.minimum_balance >= savings.minimum_required_balance
                   and baseline.essential_payments_covered is True)

    portfolio = None
    investment_cash_events = []
    investment_trades = {}
    buy_total = sell_total = 0
    if req.investment_trades:
        portfolio = PortfolioSimulationRequest(
            user_id=req.user_id, as_of=as_of, horizon_days=req.horizon_days,
            trades=req.investment_trades, price_marks=req.investment_price_marks,
            minimum_required_balance=savings.minimum_required_balance,
            forecast_daily_spend_override=savings.forecast_daily_spend_override,
            valuation_loss_percent=req.valuation_loss_percent,
        )
        investment_trades, receipts, investment_cash_events, buy_total, sell_total, _, _, _ = (
            _validate_and_prepare(portfolio, as_of, limit, account))

    purchases = _purchase_options(req, as_of, limit)
    savings_variants = _candidate_plans(savings, as_of)
    investment_modes = [True, False] if portfolio and not req.investment_required else [bool(portfolio)]
    options = []
    failures = Counter()
    requested_checks: dict[str, bool] = {}
    evaluated = valid_count = 0
    for (purchase_kind, purchase_day), (saving_kind, saving_label, saving_request), invested in product(
            purchases, savings_variants, investment_modes):
        start, maturity, deposits, _, _, reached, maturity_amount = savings_projection(saving_request, as_of)
        actions = []
        if purchase_day is not None:
            actions.append(_event(req.user_id, account, "purchase", purchase_day,
                                  -req.purchase_amount, "purchase"))
        for index, (pay_day, value) in enumerate(deposits):
            if pay_day <= limit:
                actions.append(_event(req.user_id, account, f"savings:{index}", pay_day,
                                      -value, "savings_deposit"))
        if invested:
            actions.extend(investment_cash_events)
        projection = calculate(ds, CalculationRequest(**shared, simulation_events=actions))
        if invested and portfolio is not None:
            investment_chart, _, market, _, realized, unrealized = _ledger(
                portfolio, projection.daily_cashflow, baseline.daily_cashflow, investment_trades)
        else:
            investment_chart, market, realized, unrealized = [], 0, 0, 0
        running_saved = savings.current_savings
        deposits_by_day = defaultdict(int)
        for pay_day, value in deposits:
            if pay_day <= limit:
                deposits_by_day[pay_day] += value
        points = []
        for index, (actual, no_action) in enumerate(
                zip(projection.daily_cashflow, baseline.daily_cashflow)):
            running_saved += deposits_by_day[actual.date]
            invested_today = investment_chart[index].investment_market_value if invested else 0
            profit_today = investment_chart[index].total_profit_loss if invested else 0
            points.append(IntegratedGoalChartPoint(
                date=actual.date,
                payment_account_balance=actual.closing_balance,
                payment_account_lowest_balance=actual.lowest_balance,
                balance_delta_vs_no_actions=actual.closing_balance - no_action.closing_balance,
                savings_principal_balance=running_saved,
                portfolio_market_value=invested_today,
                portfolio_total_profit_loss=profit_today,
                total_virtual_assets=running_saved + invested_today,
            ))
        deposited_in_window = sum(value for pay_day, value in deposits if pay_day <= limit)
        settled_sale_cash = (sum(t.quantity * t.unit_price for t in req.investment_trades
                                 if t.side == "sell"
                                 and (t.settlement_date or t.trade_date) <= limit)
                             if invested else 0)
        expected_delta = (-req.purchase_amount if purchase_day else 0) - deposited_in_window
        if invested:
            expected_delta += settled_sale_cash - buy_total
        if points and points[-1].balance_delta_vs_no_actions != expected_delta:
            raise FinanceProblem("integrated_goal_cash_reconciliation_failed")
        first_shortfall = (as_of if projection.available_cash < savings.minimum_required_balance else
                           next((day.date for day in projection.daily_cashflow
                                 if day.lowest_balance < savings.minimum_required_balance), None))
        checks = {
            "minimum_balance": projection.minimum_balance >= savings.minimum_required_balance,
            "essential_payments": projection.essential_payments_covered is True,
            "required_purchase": not req.purchase_required or purchase_day is not None,
            "required_investment": not req.investment_required or invested,
            "required_savings_goal": (not req.savings_goal_required_by_maturity
                                      or reached is not None),
            "savings_deposit_validated": (not deposits or deposited_in_window > 0),
        }
        ok = all(checks.values())
        if ok:
            valid_count += 1
        else:
            failures.update(key for key, passed in checks.items() if not passed)
        reasoning = []
        if not checks["minimum_balance"]:
            reasoning.append("최소 결제계좌 잔액 조건을 충족하지 못합니다.")
        if not checks["essential_payments"]:
            reasoning.append("필수 지출 보호 조건을 충족하지 못합니다.")
        if not checks["savings_deposit_validated"]:
            reasoning.append("검증 기간에 예정된 적금 납입일이 없어 이 납입안을 안전하다고 판단할 수 없습니다.")
        if reached is None:
            reasoning.append(f"만기 납입 가정으로도 목표까지 {max(0, savings.goal_amount - maturity_amount):,}원이 부족합니다.")
        else:
            reasoning.append(f"설정한 적금 납입 가정으로 {reached.isoformat()}에 저축 목표를 달성합니다.")
        if invested:
            reasoning.append("투자 평가손익은 가정한 단가 기준이며 실제 결제계좌 현금이 아닙니다.")
        if maturity > limit:
            reasoning.append("적금 만기까지의 모든 납입을 검증한 것은 아닙니다.")
        if not reasoning:
            reasoning.append("검증 기간에 입력한 제약조건을 충족합니다.")
        route = IntegratedGoalRoute(
            id=f"goal-{evaluated+1:02d}",
            label=f"{purchase_kind} · {saving_label} · " +
                  ("다종목 가상투자" if invested else "투자 보류"),
            purchase_strategy=purchase_kind, savings_strategy=saving_kind,
            investment_included=invested,
            purchase_amount=req.purchase_amount if purchase_day else 0,
            savings_deposit_amount=saving_request.deposit_amount,
            savings_start_date=start,
            investment_buy_amount=buy_total if invested else 0,
            investment_sell_proceeds=sell_total if invested else 0,
            investment_market_value=market,
            investment_total_profit_loss=realized + unrealized,
            projected_savings_at_maturity=maturity_amount,
            savings_goal_reached_by_maturity_on_assumptions=reached is not None,
            remaining_savings_goal_amount=max(0, savings.goal_amount - maturity_amount),
            payment_account_minimum_balance=projection.minimum_balance,
            payment_account_ending_balance=points[-1].payment_account_balance,
            first_shortfall_date=first_shortfall,
            checks=checks, all_hard_constraints_met=ok,
            reasoning=reasoning, chart=points,
        )
        if evaluated == 0:
            requested_checks = checks
        evaluated += 1
        if ok:
            options.append(route)

    explanation = []
    if not baseline_ok:
        explanation.append("소비·적금·투자를 하지 않아도 필수결제 또는 최소 잔액 조건에 위험이 있습니다.")
        status = "base_cashflow_at_risk"
        selected = []
    elif not options:
        status = "no_valid_route"
        explanation.append("지정한 목표와 필수결제 조건을 모두 만족하는 조합을 찾지 못했습니다.")
        for failure, count in failures.most_common(3):
            explanation.append(f"{failure} 제약에 걸린 시나리오: {count}개")
        selected = []
    else:
        status = "ok"
        options.sort(key=lambda p: _sort_key(p, savings.current_savings >= savings.goal_amount))
        # Favor distinct purchase/investment families so the client can show real alternatives.
        selected = []
        families = set()
        for choice in options:
            family = (choice.purchase_strategy, choice.investment_included)
            if family not in families:
                selected.append(choice)
                families.add(family)
            if len(selected) >= req.max_results:
                break
        for choice in options:
            if len(selected) >= req.max_results:
                break
            if choice not in selected:
                selected.append(choice)
    if status != "ok" and not explanation:
        explanation.append("제약조건을 만족하는 검증된 추천 경로가 없습니다.")
    return IntegratedGoalResult(
        user_id=req.user_id, as_of=as_of, dataset_id=ds.meta["dataset_id"],
        status=status,
        recommended_route_id=selected[0].id if selected else None,
        routes=selected, requested_plan_checks=requested_checks,
        evaluated_count=evaluated, valid_count=valid_count,
        baseline_minimum_payment_account_balance=baseline.minimum_balance,
        baseline_essential_payments_covered=baseline.essential_payments_covered is True,
        conflict_explanations=explanation,
        assumptions=[*IntegratedGoalResult.model_fields["assumptions"].default_factory(),
                     f"cashflow_forecast_source:{source}",
                     "historical_synthetic_data_not_real_user_financial_advice"],
    )

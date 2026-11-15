"""Multi-stock/ETF hypothetical portfolio, reconciled to the canonical payment-account engine.

No trades persist, no observed/mock holdings are liquidated, and no returns are
fabricated. Only caller-provided trade prices, marks and loss assumptions are used.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from app.ml.forecast import ForecastProblem, run_forecast, to_events
from app.portfolio_models import (
    PortfolioDailyPoint, PortfolioPosition, PortfolioPriceMark,
    PortfolioSimulationRequest, PortfolioSimulationResult, PortfolioTrade,
    PortfolioTradeReceipt,
)
from app.schemas import CalculationRequest, ExternalEvent
from app.services.dataset import Dataset
from app.services.finance import FinanceProblem, calculate, midday


def _forecast(ds: Dataset, uid: str, day: date, horizon: int, override: int | None):
    user = ds.get_user(uid)
    if override is not None:
        return (
            [ExternalEvent(
                id=f"portfolio:spend:{d.isoformat()}", user_id=uid,
                account_id=user.profile["payment_account_id"], debit_at=midday(d),
                amount=-override, kind="variable_expense_assumption",
                label="직접 입력한 일일 변동지출", status="predicted", source="rules",
                payment_method="debit", forecast_run_id=f"portfolio:explicit:{day}",
            ) for d in (day + timedelta(days=i) for i in range(1, horizon + 1))],
            "explicit_daily_spend_assumption_not_ml",
        )
    try:
        forecast = run_forecast(ds, uid, day, horizon)
    except ForecastProblem as exc:
        raise FinanceProblem(f"portfolio_forecast_unavailable:{exc}") from exc
    if forecast.get("model_type") == "unavailable":
        raise FinanceProblem("portfolio_spend_forecast_has_no_valid_observations")
    return to_events(user, forecast), f"{forecast['model_type']}:{forecast.get('model_version', 'unknown')}"


def _validate_and_prepare(req: PortfolioSimulationRequest, as_of: date,
                          horizon_end: date, account: str):
    """Validate trade chronology/holdings before any cashflow is simulated."""
    ordered = sorted(enumerate(req.trades), key=lambda pair: (pair[1].trade_date, pair[0]))
    held: dict[str, int] = defaultdict(int)
    codes = {t.code for t in req.trades}
    first_trades = {code: min(t.trade_date for t in req.trades if t.code == code) for code in codes}
    for mark in req.price_marks:
        if mark.code not in codes:
            raise FinanceProblem(f"portfolio_mark_without_traded_code:{mark.code}")
        if not (as_of < mark.date <= horizon_end):
            raise FinanceProblem("portfolio_price_mark_outside_simulation_window")
        if mark.date < first_trades[mark.code]:
            raise FinanceProblem("portfolio_price_mark_before_first_hypothetical_trade")
    receipts: list[PortfolioTradeReceipt] = []
    cash_events: list[ExternalEvent] = []
    by_day: dict[date, list[PortfolioTrade]] = defaultdict(list)
    bought = sold = settled = pending = 0
    default_settlement = False

    for _, trade in ordered:
        if not (as_of < trade.trade_date <= horizon_end):
            raise FinanceProblem("portfolio_trade_outside_simulation_window")
        if trade.side == "sell" and held[trade.code] < trade.quantity:
            raise FinanceProblem(f"portfolio_sell_exceeds_hypothetical_holdings:{trade.code}")
        held[trade.code] += trade.quantity if trade.side == "buy" else -trade.quantity
        gross = trade.quantity * trade.unit_price
        if trade.side == "buy":
            bought += gross
            settlement = trade.trade_date
        else:
            sold += gross
            settlement = trade.settlement_date or trade.trade_date
            default_settlement |= trade.settlement_date is None
            if settlement <= horizon_end:
                settled += gross
            else:
                pending += gross
        by_day[trade.trade_date].append(trade)
        receipts.append(PortfolioTradeReceipt(
            trade_id=trade.trade_id, code=trade.code, asset_type=trade.asset_type,
            side=trade.side, trade_date=trade.trade_date,
            settlement_date=settlement, quantity=trade.quantity,
            unit_price=trade.unit_price, gross_amount=gross,
            cash_in_validation_window=settlement <= horizon_end,
        ))
        cash_events.append(ExternalEvent(
            id=f"portfolio:{req.user_id}:{trade.trade_id}", user_id=req.user_id,
            account_id=account, debit_at=midday(settlement),
            amount=-gross if trade.side == "buy" else gross,
            kind="virtual_investment_buy" if trade.side == "buy" else "virtual_investment_sell",
            label=f"가상 {trade.asset_type} {trade.side}: {trade.code}",
            status="simulated", source="simulation", payment_method="transfer",
        ))
    return by_day, receipts, cash_events, bought, sold, settled, pending, default_settlement


def _ledger(req: PortfolioSimulationRequest, days, baseline_days,
            trades_by_day: dict[date, list[PortfolioTrade]]):
    state: dict[str, dict] = {}
    marks_by_day: dict[date, list[PortfolioPriceMark]] = defaultdict(list)
    for mark in req.price_marks:
        marks_by_day[mark.date].append(mark)
    chart: list[PortfolioDailyPoint] = []
    for cash_day, baseline_day in zip(days, baseline_days):
        today = cash_day.date
        for trade in trades_by_day.get(today, []):
            slot = state.setdefault(trade.code, {
                "asset_type": trade.asset_type, "qty": 0, "cost": 0,
                "realized": 0, "price": None, "source": "no_price_assumption",
            })
            if trade.side == "buy":
                slot["qty"] += trade.quantity
                slot["cost"] += trade.quantity * trade.unit_price
            else:
                # Average-cost accounting. Integer KRW rounding is deterministic.
                q = slot["qty"]
                if q < trade.quantity:
                    raise FinanceProblem("portfolio_sell_exceeds_hypothetical_holdings")
                released = (slot["cost"] * trade.quantity + q // 2) // q
                slot["realized"] += trade.quantity * trade.unit_price - released
                slot["cost"] -= released
                slot["qty"] -= trade.quantity
            slot["price"] = trade.unit_price
            slot["source"] = "user_trade_price_carried_forward"
        for mark in marks_by_day.get(today, []):
            if mark.code in state:
                state[mark.code]["price"] = mark.unit_price
                state[mark.code]["source"] = "explicit_user_valuation_mark"
        market = unrealized = realized = 0
        for slot in state.values():
            base_price = slot["price"]
            if base_price is None and slot["qty"]:
                raise FinanceProblem("portfolio_missing_valuation_reference")
            price = ((base_price * (100 - req.valuation_loss_percent) + 50) // 100
                     if base_price is not None else 0)
            value = slot["qty"] * price
            market += value
            unrealized += value - slot["cost"]
            realized += slot["realized"]
        chart.append(PortfolioDailyPoint(
            date=today,
            payment_account_balance=cash_day.closing_balance,
            payment_account_lowest_balance=cash_day.lowest_balance,
            balance_delta_vs_no_trades=cash_day.closing_balance - baseline_day.closing_balance,
            investment_market_value=market, realized_profit_loss=realized,
            unrealized_profit_loss=unrealized, total_profit_loss=realized + unrealized,
        ))

    positions: list[PortfolioPosition] = []
    total_value = total_cost = total_realized = total_unrealized = 0
    for code, slot in sorted(state.items()):
        qty, cost = slot["qty"], slot["cost"]
        base_price = slot["price"]
        valuation = ((base_price * (100 - req.valuation_loss_percent) + 50) // 100
                     if base_price is not None else None)
        value = qty * (valuation or 0)
        unrealized = value - cost
        realized = slot["realized"]
        total_value += value
        total_cost += cost
        total_realized += realized
        total_unrealized += unrealized
        positions.append(PortfolioPosition(
            code=code, asset_type=slot["asset_type"], quantity=qty,
            cost_basis=cost,
            average_purchase_price=round(cost / qty, 2) if qty else None,
            valuation_unit_price=valuation if qty else None,
            valuation_price_source=(slot["source"] if qty else "position_closed"),
            market_value=value, realized_profit_loss=realized,
            unrealized_profit_loss=unrealized, total_profit_loss=realized + unrealized,
            unrealized_return_percent=round(100 * unrealized / cost, 2) if cost else None,
        ))
    if chart and (chart[-1].investment_market_value != total_value
                  or chart[-1].total_profit_loss != total_realized + total_unrealized):
        raise FinanceProblem("portfolio_chart_and_positions_mismatch")
    return chart, positions, total_value, total_cost, total_realized, total_unrealized


def simulate_portfolio(ds: Dataset, req: PortfolioSimulationRequest) -> PortfolioSimulationResult:
    if req.user_id not in ds.profiles:
        raise KeyError(req.user_id)
    begin = date.fromisoformat(ds.meta["start_date"])
    # A later confirmed SMS is not a verified synthetic investment price feed.
    cutoff = date.fromisoformat(ds.meta.get("canonical_snapshot_date", ds.meta["snapshot_date"]))
    as_of = req.as_of or cutoff
    if not begin <= as_of <= cutoff:
        raise FinanceProblem("portfolio_observation_date_out_of_range")
    horizon_end = as_of + timedelta(days=req.horizon_days)
    account = ds.get_user(req.user_id).profile["payment_account_id"]
    by_day, receipts, events, bought, sold, settled, pending, same_day = _validate_and_prepare(
        req, as_of, horizon_end, account)
    forecast, source = _forecast(ds, req.user_id, as_of, req.horizon_days,
                                req.forecast_daily_spend_override)
    shared = dict(user_id=req.user_id, account_id=account, as_of=as_of,
                  horizon_days=req.horizon_days,
                  minimum_required_balance=req.minimum_required_balance,
                  forecast_events=forecast)
    baseline = calculate(ds, CalculationRequest(**shared))
    result = calculate(ds, CalculationRequest(**shared, simulation_events=events))
    chart, positions, market, cost, realized, unrealized = _ledger(
        req, result.daily_cashflow, baseline.daily_cashflow, by_day)
    net = settled - bought
    if chart and chart[-1].balance_delta_vs_no_trades != net:
        raise FinanceProblem("portfolio_cash_reconciliation_failed")
    if total_pnl := (realized + unrealized):
        # Total net profit/loss must equal marked holdings + *all* executed
        # sale proceeds minus purchases, including sales awaiting settlement.
        if total_pnl != market + sold - bought:
            raise FinanceProblem("portfolio_profit_loss_reconciliation_failed")
    elif market + sold - bought != 0:
        raise FinanceProblem("portfolio_profit_loss_reconciliation_failed")
    valid = (result.minimum_balance >= req.minimum_required_balance
             and result.essential_payments_covered is True)
    shortfall = (as_of if result.available_cash < req.minimum_required_balance else
                 next((item.date for item in result.daily_cashflow
                       if item.lowest_balance < req.minimum_required_balance), None))
    warnings = [
        "hypothetical_only_no_real_securities_orders_or_bank_transfers",
        "trade_and_valuation_prices_are_user_assumptions_not_live_quotes",
        "no_existing_dataset_portfolio_holdings_are_spendable_or_auto_sold",
        "fees_taxes_and_dividends_not_included",
        "future_market_prices_not_predicted",
    ]
    if same_day:
        warnings.append("sale_settlement_defaults_to_same_day_sandbox_assumption")
    if pending:
        warnings.append("some_sale_proceeds_settle_after_the_cashflow_window")
    if req.valuation_loss_percent:
        warnings.append("additional_portfolio_valuation_loss_is_hypothetical_only")
    if baseline.minimum_balance < req.minimum_required_balance or not baseline.essential_payments_covered:
        warnings.append("baseline_payment_account_at_risk_even_without_portfolio_trades")
    if not valid:
        warnings.append("payment_account_or_essential_bills_at_risk_with_simulated_trades")
    return PortfolioSimulationResult(
        user_id=req.user_id, as_of=as_of, dataset_id=ds.meta["dataset_id"],
        account_id=account, available_cash=result.available_cash,
        horizon_days=req.horizon_days, forecast_source=source,
        positions=positions, trades=receipts, daily_chart=chart,
        total_buy_amount=bought, total_sell_proceeds=sold,
        settled_sell_proceeds=settled, unsettled_sell_proceeds=pending,
        net_payment_account_cash_impact=net,
        market_value=market, remaining_cost_basis=cost,
        realized_profit_loss=realized, unrealized_profit_loss=unrealized,
        total_profit_loss=realized + unrealized,
        minimum_payment_account_balance=result.minimum_balance,
        baseline_minimum_payment_account_balance=baseline.minimum_balance,
        minimum_required_balance_met=result.minimum_balance >= req.minimum_required_balance,
        essential_payments_covered=result.essential_payments_covered is True,
        first_shortfall_date=shortfall,
        cashflow_validation_status="satisfies_assumptions" if valid else "fails_assumptions",
        warnings=warnings,
    )

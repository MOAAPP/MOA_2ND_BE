"""Typed contract for an isolated, hypothetical multi-asset portfolio simulation.

Prices and trades are explicit user assumptions, never live market quotes.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PortfolioTrade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    code: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9._-]+$")
    asset_type: Literal["stock", "etf"]
    side: Literal["buy", "sell"]
    trade_date: date
    quantity: int = Field(ge=1, le=1_000_000)
    unit_price: int = Field(ge=1, le=1_000_000_000)
    settlement_date: date | None = Field(
        default=None,
        description="매도 정산일. 생략하면 당일 정산으로 가정하며, 매수는 거래일에 결제계좌에서 즉시 차감.")


class PortfolioPriceMark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9._-]+$")
    date: date
    unit_price: int = Field(ge=0, le=1_000_000_000,
                             description="사용자가 가정한 평가 단가. 0원은 전액 손실 시나리오.")


class PortfolioSimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [
        {"user_id": "S01", "as_of": "2026-11-04", "horizon_days": 14,
         "forecast_daily_spend_override": 0,
         "trades": [
            {"trade_id": "buy-etf", "code": "ETF-A", "asset_type": "etf",
             "side": "buy", "trade_date": "2026-11-05", "quantity": 2, "unit_price": 10000},
            {"trade_id": "buy-stock", "code": "STOCK-B", "asset_type": "stock",
             "side": "buy", "trade_date": "2026-11-06", "quantity": 1, "unit_price": 5000}
         ],
         "price_marks": [
            {"code": "ETF-A", "date": "2026-11-12", "unit_price": 9000},
            {"code": "STOCK-B", "date": "2026-11-12", "unit_price": 5500}
         ]}
    ]})
    user_id: str = Field(pattern=r"^S\d\d$")
    as_of: date | None = None
    horizon_days: int = Field(default=30, ge=14, le=90)
    trades: list[PortfolioTrade] = Field(min_length=1, max_length=60)
    price_marks: list[PortfolioPriceMark] = Field(default_factory=list, max_length=200)
    minimum_required_balance: int = Field(default=0, ge=0)
    forecast_daily_spend_override: int | None = Field(default=None, ge=0)
    valuation_loss_percent: int = Field(
        default=0, ge=0, le=100,
        description="모든 보유자산 평가가격에만 적용하는 가상 손실 비율 (실제 매매·결제대금과 별개).")

    @model_validator(mode="after")
    def unique_identifiers(self):
        ids = [t.trade_id for t in self.trades]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_portfolio_trade_id")
        marks = [(m.code, m.date) for m in self.price_marks]
        if len(marks) != len(set(marks)):
            raise ValueError("duplicate_portfolio_price_mark")
        if len({t.code for t in self.trades}) > 12:
            raise ValueError("portfolio_maximum_12_instruments")
        types: dict[str, str] = {}
        for trade in self.trades:
            previous = types.setdefault(trade.code, trade.asset_type)
            if previous != trade.asset_type:
                raise ValueError("inconsistent_asset_type_for_same_code")
            if trade.side == "buy" and trade.settlement_date not in (None, trade.trade_date):
                raise ValueError("buy_cash_debit_is_on_trade_date")
            if trade.side == "sell" and trade.settlement_date is not None and trade.settlement_date < trade.trade_date:
                raise ValueError("sell_settlement_before_trade")
        return self


class PortfolioPosition(BaseModel):
    code: str
    asset_type: Literal["stock", "etf"]
    quantity: int
    cost_basis: int
    average_purchase_price: float | None
    valuation_unit_price: int | None
    valuation_price_source: str
    market_value: int
    realized_profit_loss: int
    unrealized_profit_loss: int
    total_profit_loss: int
    unrealized_return_percent: float | None


class PortfolioDailyPoint(BaseModel):
    date: date
    payment_account_balance: int
    payment_account_lowest_balance: int
    balance_delta_vs_no_trades: int
    investment_market_value: int
    realized_profit_loss: int
    unrealized_profit_loss: int
    total_profit_loss: int


class PortfolioTradeReceipt(BaseModel):
    trade_id: str
    code: str
    asset_type: Literal["stock", "etf"]
    side: Literal["buy", "sell"]
    trade_date: date
    settlement_date: date
    quantity: int
    unit_price: int
    gross_amount: int
    cash_in_validation_window: bool


class PortfolioSimulationResult(BaseModel):
    user_id: str
    as_of: date
    dataset_id: str
    source: Literal["hypothetical_synthetic_dataset"] = "hypothetical_synthetic_dataset"
    account_id: str
    available_cash: int
    horizon_days: int
    forecast_source: str
    positions: list[PortfolioPosition]
    trades: list[PortfolioTradeReceipt]
    daily_chart: list[PortfolioDailyPoint]
    total_buy_amount: int
    total_sell_proceeds: int
    settled_sell_proceeds: int
    unsettled_sell_proceeds: int
    net_payment_account_cash_impact: int
    market_value: int
    remaining_cost_basis: int
    realized_profit_loss: int
    unrealized_profit_loss: int
    total_profit_loss: int
    minimum_payment_account_balance: int
    baseline_minimum_payment_account_balance: int
    minimum_required_balance_met: bool
    essential_payments_covered: bool
    first_shortfall_date: date | None
    cashflow_validation_status: Literal["satisfies_assumptions", "fails_assumptions"]
    warnings: list[str]

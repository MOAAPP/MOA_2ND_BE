"""A single contract for goal-led purchase, recurring savings and multi-asset plans.

The request is explicit and inspectable. Existing LLM natural-language interpretation
can produce these fields later; this API never treats LLM guesses as prices or cash.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.portfolio_models import PortfolioPriceMark, PortfolioTrade
from app.stage3_models import SavingsPlanRequest


class IntegratedGoalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "user_id": "S01", "as_of": "2026-11-04", "horizon_days": 30,
        "savings_plan": {"user_id": "S01", "as_of": "2026-11-04",
                         "goal_amount": 600000, "current_savings": 200000,
                         "deposit_amount": 70000, "duration_months": 6,
                         "forecast_daily_spend_override": 0},
        "purchase_amount": 5000, "purchase_required": False,
        "investment_trades": [
            {"trade_id": "buy-etf", "code": "ETF-A", "asset_type": "etf",
             "side": "buy", "trade_date": "2026-11-05", "quantity": 1, "unit_price": 10000},
            {"trade_id": "buy-stock", "code": "STOCK-B", "asset_type": "stock",
             "side": "buy", "trade_date": "2026-11-06", "quantity": 1, "unit_price": 5000}
        ],
        "investment_price_marks": [
            {"code": "ETF-A", "date": "2026-11-16", "unit_price": 9000}
        ]
    }]})
    user_id: str = Field(pattern=r"^S\d\d$")
    as_of: date | None = None
    horizon_days: int = Field(default=30, ge=14, le=90)
    savings_plan: SavingsPlanRequest
    purchase_amount: int = Field(default=0, ge=0)
    purchase_date: date | None = None
    purchase_required: bool = False
    purchase_delay_days: int = Field(default=14, ge=1, le=90)
    investment_trades: list[PortfolioTrade] = Field(default_factory=list, max_length=60)
    investment_price_marks: list[PortfolioPriceMark] = Field(default_factory=list, max_length=200)
    investment_required: bool = False
    valuation_loss_percent: int = Field(default=0, ge=0, le=100)
    savings_goal_required_by_maturity: bool = False
    max_results: int = Field(default=4, ge=1, le=6)

    @model_validator(mode="after")
    def check_consistency(self):
        if self.savings_plan.user_id != self.user_id:
            raise ValueError("integrated_goal_user_ids_must_match")
        if self.as_of is not None and self.savings_plan.as_of is not None and self.as_of != self.savings_plan.as_of:
            raise ValueError("integrated_goal_observation_dates_must_match")
        if self.purchase_required and self.purchase_amount == 0:
            raise ValueError("required_purchase_amount_missing")
        if self.investment_required and not self.investment_trades:
            raise ValueError("required_investment_trades_missing")
        if not self.investment_trades and self.investment_price_marks:
            raise ValueError("price_marks_without_investment_trades")
        return self


class IntegratedGoalChartPoint(BaseModel):
    date: date
    payment_account_balance: int
    payment_account_lowest_balance: int
    balance_delta_vs_no_actions: int
    savings_principal_balance: int
    portfolio_market_value: int
    portfolio_total_profit_loss: int
    total_virtual_assets: int


class IntegratedGoalRoute(BaseModel):
    id: str
    label: str
    purchase_strategy: Literal["purchase_now", "purchase_delay", "no_purchase"]
    savings_strategy: str
    investment_included: bool
    purchase_amount: int
    savings_deposit_amount: int
    savings_start_date: date
    investment_buy_amount: int
    investment_sell_proceeds: int
    investment_market_value: int
    investment_total_profit_loss: int
    projected_savings_at_maturity: int
    savings_goal_reached_by_maturity_on_assumptions: bool
    remaining_savings_goal_amount: int
    payment_account_minimum_balance: int
    payment_account_ending_balance: int
    first_shortfall_date: date | None
    checks: dict[str, bool]
    all_hard_constraints_met: bool
    reasoning: list[str]
    chart: list[IntegratedGoalChartPoint]


class IntegratedGoalResult(BaseModel):
    user_id: str
    as_of: date
    dataset_id: str
    status: Literal["ok", "no_valid_route", "base_cashflow_at_risk"]
    recommended_route_id: str | None
    routes: list[IntegratedGoalRoute]
    requested_plan_checks: dict[str, bool]
    evaluated_count: int
    valid_count: int
    baseline_minimum_payment_account_balance: int
    baseline_essential_payments_covered: bool
    conflict_explanations: list[str]
    assumptions: list[str] = Field(default_factory=lambda: [
        "가상 시나리오만 계산하며 실제 매수·매도·적금 자동이체를 수행하지 않습니다.",
        "투자 매매가격·평가단가와 저축 납입일은 입력한 가정입니다. 시장 수익률을 예측하지 않습니다.",
        "저축 만기 금액은 납입 계획의 산술적 예측입니다. 계좌 잔액은 요청한 최대 90일만 검증합니다.",
        "수익률·손실이 결제계좌 잔액에 자동으로 더해지지 않으며, 매도대금은 지정한 정산일에만 반영됩니다.",
    ])

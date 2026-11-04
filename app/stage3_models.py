"""Stage 3: independent, fully validated hypothetical-world requests.

The input dataset remains immutable. Amounts are in integer KRW.
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

EventKind = Literal[
    "purchase", "no_purchase", "purchase_delay", "savings_deposit",
    "savings_adjustment", "virtual_investment_buy", "virtual_investment_sell",
    "virtual_asset_revaluation", "income_delay", "adjustable_expense_change",
]
WorldKind = Literal[
    "purchase_now", "no_purchase", "purchase_delay",
    "virtual_savings", "virtual_investment", "mixed",
]
StressKind = Literal["baseline", "higher_spending", "delayed_income", "combined", "investment_loss"]


class ScenarioEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    event_type: EventKind
    effective_at: date
    debit_at: datetime | None = None
    account_id: str | None = None
    payment_method: str = "transfer"
    amount: int = Field(ge=0)
    source: Literal["user", "simulation", "agent"] = "simulation"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_debit(self):
        if self.debit_at is not None and self.debit_at.tzinfo is None:
            raise ValueError("debit_at must include timezone")
        return self


class Allocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purchase: int = Field(default=0, ge=0)
    savings: int = Field(default=0, ge=0)
    investment: int = Field(default=0, ge=0)
    cash: int = Field(default=0, ge=0)

    @property
    def total(self):
        return self.purchase + self.savings + self.investment + self.cash


class WorldCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(pattern=r"^S\d\d$")
    as_of: date | None = None
    horizon_days: int = Field(default=30, ge=14, le=90)
    purchase_amount: int = Field(ge=0)
    purchase_date: date | None = None
    purchase_delay_days: int = Field(default=7, ge=1, le=90)
    payment_method: Literal["debit", "transfer", "credit"] = "debit"
    credit_debit_date: date | None = None
    delayed_credit_debit_date: date | None = None
    savings_amount: int | None = Field(default=None, ge=0)
    savings_date: date | None = None
    savings_monthly_amount: int = Field(default=0, ge=0)
    savings_goal_target: int | None = Field(default=None, ge=1)
    savings_goal_current: int = Field(default=0, ge=0)
    savings_annual_rate: float | None = Field(default=None, ge=0, le=1)
    investment_amount: int | None = Field(default=None, ge=0)
    investment_code: str | None = None
    investment_unit_price: int | None = Field(default=None, gt=0)
    investment_return_percent: float = Field(default=0, ge=-100, le=1000)
    allocation: Allocation | None = None
    minimum_required_balance: int = Field(default=0, ge=0)
    stress: StressKind = "baseline"
    daily_spend_increase_percent: float = Field(default=50, ge=0, le=500)
    income_delay_days: int = Field(default=3, ge=0, le=31)
    forecast_daily_spend_override: int | None = Field(default=None, ge=0)
    custom_events: list[ScenarioEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_request(self):
        if self.allocation and self.allocation.total > self.purchase_amount:
            raise ValueError("allocation_exceeds_decision_budget")
        return self


class ScenarioDay(BaseModel):
    date: date
    opening_balance: int
    inflow: int
    outflow: int
    closing_balance: int
    lowest_balance: int
    savings_balance: int
    investment_value: int
    investment_profit_loss: int


class ScenarioPath(BaseModel):
    scenario_id: WorldKind
    label: str
    status: Literal["calculated", "needs_confirmation", "invalid"]
    events: list[ScenarioEvent]
    available_cash: int
    future_balances: dict[str, int]
    daily_cashflow: list[ScenarioDay]
    minimum_balance: int
    first_shortfall_date: date | None
    minimum_balance_met: bool
    essential_payments_covered: bool
    virtual_savings: int
    virtual_investment_value: int
    virtual_investment_profit_loss: int
    investment_quantity: int
    investment_average_price: float | None
    savings_goal_reached_date: date | None
    purchase_completed: bool
    purchase_delay_days: int
    assumptions: list[str] = Field(default_factory=list)


class WorldCompareResult(BaseModel):
    dataset_id: str
    user_id: str
    as_of: date
    observation_start: date
    observation_end: date
    source: Literal["synthetic_dataset"] = "synthetic_dataset"
    baseline_source: str
    baseline_daily_variable_expense: int
    account_id: str
    available_cash: int
    reference_investment_price: int | None
    reference_investment_code: str | None
    paths: list[ScenarioPath]
    warnings: list[str] = Field(default_factory=list)


class SavedScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=120)
    user_id: str
    request: WorldCompareRequest
    saved_at: datetime

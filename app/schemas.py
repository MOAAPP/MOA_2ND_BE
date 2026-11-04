"""Financial facts, forecasts and hypothetical events stay explicitly separated."""
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExternalEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1)
    user_id: str
    account_id: str
    debit_at: datetime
    occurred_at: datetime | None = None
    amount: int = Field(description='Signed KRW: income positive, cash debit negative')
    kind: str
    label: str
    essential: bool = False
    payment_method: str = 'transfer'
    status: Literal['predicted', 'simulated']
    source: Literal['model', 'rules', 'simulation']
    forecast_run_id: str | None = None

    @model_validator(mode='after')
    def check_source(self):
        if (self.status, self.source) not in {('predicted', 'model'), ('predicted', 'rules'), ('simulated', 'simulation')}:
            raise ValueError('predicted requires model source; simulated requires simulation source')
        if self.source == 'model' and not self.forecast_run_id:
            raise ValueError('forecast_run_id required for predicted event')
        if self.debit_at.tzinfo is None or (self.occurred_at and self.occurred_at.tzinfo is None):
            raise ValueError('timezone-aware timestamps required')
        return self


class CalculationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str = Field(pattern=r'^S\d\d$')
    account_id: str | None = None
    as_of: date | datetime | None = None
    horizon_days: int = Field(default=14, ge=1, le=90)
    minimum_required_balance: int | None = Field(default=None, ge=0)
    include_uncertain_income: bool = False
    income_delay_days: int = Field(default=0, ge=0, le=31)
    forecast_events: list[ExternalEvent] = Field(default_factory=list)
    simulation_events: list[ExternalEvent] = Field(default_factory=list)


class CashEvent(BaseModel):
    id: str
    user_id: str
    account_id: str
    occurred_at: datetime | None
    debit_at: datetime
    amount: int
    label: str
    kind: str
    payment_method: str
    transaction_status: Literal['actual', 'scheduled', 'predicted', 'simulated']
    transaction_source: str
    essential: bool = False
    source_transaction_ids: list[str] = Field(default_factory=list)


class DailyCashflow(BaseModel):
    date: date
    opening_balance: int
    inflow: int
    outflow: int
    lowest_balance: int
    closing_balance: int
    events: list[CashEvent]


class FinancialResult(BaseModel):
    dataset_id: str
    user_id: str
    account_id: str
    as_of: datetime
    balance_snapshot_at: datetime
    balance_snapshot_source: str
    available_cash: int
    minimum_required_balance: int | None
    future_balances: dict[str, int]
    daily_cashflow: list[DailyCashflow]
    minimum_balance: int
    minimum_cash_cushion: int | None
    essential_payments_covered: bool | None
    essential_payment_results: list[dict]
    warnings: list[str]
    virtual_investments: dict
    savings_goals: list
    planned_purchases: list
    observed_transaction_count: int
    excluded_oracle_transaction_count: int
    forecast_applied: bool


class UserState(BaseModel):
    dataset_id: str
    user_id: str
    account_id: str
    balance_snapshot_at: datetime
    balance_snapshot_source: str
    available_cash: int
    observation_start: date
    observation_end: date
    accounts: list[dict]
    scheduled_income: list[dict]
    fixed_expenses: list[dict]
    credit_card_bills: list[dict]
    variable_expense_count: int
    savings_goals: list
    virtual_investments: dict
    purchase_candidates: list[dict]
    minimum_required_balance: int | None
    warnings: list[str]
"""Stateless, input-only models for USER mode (never synthetic S01/S08).

No names, email, SMS text, bank numbers or merchant descriptions are accepted.
These endpoints are dev/preview simulations, not authenticated banking.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PersonalBill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: int = Field(ge=1, le=31)
    amount: int = Field(ge=1, le=100_000_000)
    kind: Literal["card", "rent", "phone", "subscription", "loan", "etc"] = "etc"


class PersonalPlannedTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    amount: int = Field(ge=1, le=100_000_000)
    kind: Literal["income", "expense", "transfer"]
    payment_method: Literal["debit", "transfer", "credit", "cash"] = "debit"


class PersonalSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of: date
    balance: int = Field(ge=0, le=10_000_000_000)
    minimum_balance: int = Field(ge=0, le=10_000_000_000)
    monthly_income: int = Field(ge=0, le=100_000_000)
    payday: int = Field(ge=1, le=31)
    # Explicit user-set variable spending budget; never represented as ML.
    monthly_variable_budget: int = Field(default=0, ge=0, le=100_000_000)
    bills: list[PersonalBill] = Field(default_factory=list, max_length=60)
    planned_transactions: list[PersonalPlannedTransaction] = Field(default_factory=list, max_length=120)

    @model_validator(mode="after")
    def check_dates(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        if self.as_of > datetime.now(ZoneInfo("Asia/Seoul")).date():
            raise ValueError("future_as_of_not_supported")
        for txn in self.planned_transactions:
            if txn.date <= self.as_of:
                raise ValueError("planned_transaction_must_be_after_balance_snapshot")
        return self


class PersonalCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot: PersonalSnapshot
    purchase_amount: int = Field(ge=1000, le=100_000_000)
    horizon_days: int = Field(default=30, ge=7, le=90)
    payment_method: Literal["debit", "transfer"] = "debit"
    purchase_delay_days: int = Field(default=30, ge=1, le=90)


class PersonalGoalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot: PersonalSnapshot
    target_amount: int = Field(ge=1000, le=100_000_000)
    horizon_days: int = Field(default=30, ge=7, le=90)
    goal_type: Literal["purchase", "savings", "investment"] = "purchase"
    investment_allowed: bool = False


class PersonalDailyPoint(BaseModel):
    date: date
    closing_balance: int
    lowest_balance: int


class PersonalPath(BaseModel):
    scenario_id: Literal[
        "purchase_now", "no_purchase", "purchase_delay",
        "virtual_savings", "virtual_investment", "mixed",
    ]
    status: Literal["calculated", "out_of_horizon"]
    available_cash: int
    ending_balance: int
    minimum_balance: int
    minimum_balance_met: bool
    essential_payments_covered: bool
    first_shortfall_date: date | None
    daily_cashflow: list[PersonalDailyPoint]


class PersonalCompareResult(BaseModel):
    source: Literal["user_entered_hypothetical"] = "user_entered_hypothetical"
    calculator: Literal["python_finance_cashflow_rules"] = "python_finance_cashflow_rules"
    as_of: date
    available_cash: int
    minimum_required_balance: int
    purchase_amount: int
    horizon_days: int
    payment_method: str
    forecast_source: Literal["user_budget_assumption_not_ml", "no_variable_spend_assumption"]
    inputs_not_saved: bool = True
    warnings: list[str]
    paths: list[PersonalPath]


class PersonalGoalResult(BaseModel):
    source: Literal["personal_rules_not_llm"] = "personal_rules_not_llm"
    as_of: date
    target_amount: int
    goal_type: str
    horizon_days: int
    evaluated_count: int
    valid_count: int
    routes: list[PersonalPath]
    rejection_counts: dict[str, int]
    confirmations_required: list[str]
    warnings: list[str]
    inputs_not_saved: bool = True

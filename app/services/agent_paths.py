"""Stage 5: deterministic constrained route search over the Stage 3 financial engine.

Only validated Python ScenarioPath objects can become recommendations. Never calculate
money inside an LLM and never read future oracle transactions as observations.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from itertools import product
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.dataset import Dataset
from app.services.finance import FinanceProblem
from app.services.stage3 import compare_worlds
from app.stage3_models import Allocation, ScenarioPath, WorldCompareRequest


class GoalConditions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(pattern=r"^S\d\d$")
    as_of: date
    target_product_id: str | None = None
    purchase_amount: int | None = Field(default=None, ge=0)
    purchase_required: bool = False
    preferred_purchase_date: date | None = None
    latest_purchase_date: date | None = None
    allowed_payment_methods: list[Literal["debit", "transfer", "credit"]] = Field(default_factory=lambda: ["debit"])
    credit_debit_date: date | None = None
    minimum_balance: int | None = Field(default=None, ge=0)
    protect_essential_bills: bool = True
    savings_goal_id: str | None = None
    required_savings_amount: int | None = Field(default=None, ge=0)
    investment_allowed: bool = False
    maximum_investment_amount: int = Field(default=0, ge=0)
    acceptable_purchase_delay_days: int | None = Field(default=None, ge=0, le=90)
    adjustable_expense_categories: list[str] = Field(default_factory=list)
    maximum_spending_reduction: int = Field(default=0, ge=0)
    hard_constraints: list[str] = Field(default_factory=list)
    soft_preferences: list[str] = Field(default_factory=list)
    confirmations_required: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistency(self):
        if self.preferred_purchase_date and self.latest_purchase_date and self.preferred_purchase_date > self.latest_purchase_date:
            raise ValueError("preferred_purchase_date_after_latest_purchase_date")
        if not self.allowed_payment_methods:
            raise ValueError("allowed_payment_methods_empty")
        return self


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conditions: GoalConditions
    horizon_days: int = Field(default=30, ge=14, le=90)
    max_evaluations: int = Field(default=72, ge=1, le=150)
    max_results: int = Field(default=4, ge=1, le=4)
    stress: Literal["baseline", "higher_spending", "delayed_income", "combined", "investment_loss"] = "baseline"
    income_delay_days: int = Field(default=3, ge=0, le=31)
    daily_spend_increase_percent: int = Field(default=25, ge=0, le=200)
    investment_return_percent: int = Field(default=0, ge=-100, le=100)


class Route(BaseModel):
    id: str
    label: str
    kind: str
    purchase_date: date | None
    payment_method: str
    purchase_amount: int
    savings_amount: int
    investment_amount: int
    spending_reduction: int = 0
    purchase_delay_days: int
    minimum_balance: int
    ending_balance: int
    savings_goal_impact: int
    investment_profit_loss: int
    investment_risk: str
    hard_constraints_met: bool
    checks: dict[str, bool]
    scenario: ScenarioPath
    engine_request: WorldCompareRequest


class SearchResult(BaseModel):
    user_id: str
    as_of: date
    dataset_id: str
    forecast_source: str | None = None
    status: Literal["ok", "no_valid_route", "needs_confirmation"]
    routes: list[Route]
    evaluated_count: int
    valid_count: int
    rejection_counts: dict[str, int]
    conflict_explanations: list[str]
    confirmations_required: list[str] = Field(default_factory=list)
    elapsed_ms: float
    fixed_baseline_valid_routes: int | None = None
    algorithm: str = "bounded_enumeration_pareto_v1"


def resolve_candidate(ds: Dataset, g: GoalConditions):
    u = ds.get_user(g.user_id)
    candidates = [x for x in u.purchase_intents if x.get("date", "") <= g.as_of.isoformat()]
    if g.target_product_id:
        matches = [c for c in candidates if str(c.get("id")) == g.target_product_id]
        if not matches:
            raise ValueError("purchase_candidate_not_found")
        candidate = matches[-1]
    else:
        candidate = sorted(candidates, key=lambda x: (x.get("date", ""), str(x.get("id", ""))))[-1] if candidates else None
    product = (candidate or {}).get("main_product", {})
    amount = g.purchase_amount if g.purchase_amount is not None else product.get("final_estimated_payment")
    if amount is not None and (type(amount) is not int or amount < 0):
        raise ValueError("invalid_purchase_price")
    return candidate, amount


def _dominates(a: Route, b: Route) -> bool:
    # Pareto: more desired purchases and savings; shorter delay, more cash,
    # less investment loss/exposure and less lifestyle sacrifice.
    def vector(r):
        return (int(r.purchase_amount > 0), r.savings_amount, r.minimum_balance,
                -r.purchase_delay_days, -r.investment_amount,
                min(0, r.investment_profit_loss), -r.spending_reduction)
    x, y = vector(a), vector(b)
    return all(u >= v for u, v in zip(x, y)) and any(u > v for u, v in zip(x, y))


def _label(purchase: int, savings: int, invest: int, delay: int) -> str:
    if purchase and savings:
        return "구매와 저축 병행" if not delay else "구매 시점 조정·저축 병행"
    if purchase:
        return "구매 우선" if delay <= 1 else "구매 시점 조정"
    if savings:
        return "저축 목표 우선"
    if invest:
        return "가상투자 포함"
    return "가용 현금 유지"


def search_paths(ds: Dataset, req: SearchRequest) -> SearchResult:
    started = perf_counter()
    g = req.conditions
    if g.user_id not in ds.profiles:
        raise KeyError(g.user_id)
    if not (date.fromisoformat(ds.meta["start_date"]) <= g.as_of <= date.fromisoformat(ds.meta["snapshot_date"])):
        raise ValueError("observation_date_out_of_range")
    candidate, price = resolve_candidate(ds, g)
    missing = list(g.confirmations_required)
    if g.purchase_required and not price:
        missing.append("구매 금액 또는 확인된 구매 후보가 필요해요.")
    if g.savings_goal_id and g.required_savings_amount is None:
        missing.append("저축을 유지할 최소 납입 금액을 확인해 주세요. 원본 데이터에 목표 금액이 없어요.")
    if "credit" in g.allowed_payment_methods and not g.credit_debit_date:
        missing.append("신용카드 결제계좌 출금일을 확인해 주세요.")
    if missing:
        return SearchResult(user_id=g.user_id, as_of=g.as_of, dataset_id=ds.meta["dataset_id"],
                            status="needs_confirmation", routes=[], evaluated_count=0,
                            valid_count=0, rejection_counts={}, conflict_explanations=[],
                            confirmations_required=list(dict.fromkeys(missing)),
                            elapsed_ms=round((perf_counter() - started) * 1000, 2))
    price = price or 0
    savings_min = g.required_savings_amount or 0
    days = [1, 3, 7, 14, 21, 30, 45, 60, 90]
    if g.preferred_purchase_date and g.preferred_purchase_date > g.as_of:
        days.append((g.preferred_purchase_date - g.as_of).days)
    dates = sorted({g.as_of + timedelta(days=d) for d in days if d <= req.horizon_days})
    dates = [d for d in dates if (not g.latest_purchase_date or d <= g.latest_purchase_date)]
    if g.acceptable_purchase_delay_days is not None and g.preferred_purchase_date:
        dates = [d for d in dates if max(0, (d - g.preferred_purchase_date).days) <= g.acceptable_purchase_delay_days]
    if not dates and g.purchase_required:
        return SearchResult(user_id=g.user_id, as_of=g.as_of, dataset_id=ds.meta["dataset_id"],
                            status="no_valid_route", routes=[], evaluated_count=0, valid_count=0,
                            rejection_counts={"purchase_deadline": 1},
                            conflict_explanations=["희망 구매일과 기한이 탐색 기간에 맞지 않아요. 날짜 조건을 직접 수정해야 해요."],
                            elapsed_ms=round((perf_counter() - started) * 1000, 2))
    methods = [x for x in g.allowed_payment_methods if x != "credit" or g.credit_debit_date]
    # Do not introduce spending reductions or investments without explicit permission.
    savings_options = [savings_min] if savings_min else [0, max(0, price // 4)]
    investments = [0]
    if g.investment_allowed and g.maximum_investment_amount:
        investments.append(min(g.maximum_investment_amount, max(1, price // 4)))
    reductions = [0]
    # "adjustable" category must be explicitly provided; no assumptions about editable spending.
    if g.adjustable_expense_categories and g.maximum_spending_reduction:
        reductions.append(g.maximum_spending_reduction)
    choices = []
    for date_choice, method, savings, investment, reduction in product(
        dates or [g.as_of + timedelta(days=1)], methods, savings_options, investments, reductions
    ):
        if price:
            choices.append((price, date_choice, method, savings, investment, reduction))
    if not g.purchase_required:
        for savings, investment, reduction in product(savings_options, investments, reductions):
            choices.append((0, None, "debit", savings, investment, reduction))
    if not choices:
        choices.append((0, None, "debit", savings_min, 0, 0))
    valid: list[Route] = []
    failures: Counter[str] = Counter()
    seen: set[str] = set()
    evaluated = 0
    origin = None
    for purchase, day, method, savings, invest, reduction in choices:
        if evaluated >= req.max_evaluations:
            failures["search_budget_reached"] += 1
            break
        if purchase == 0 and g.purchase_required:
            continue
        if g.preferred_purchase_date and day and day < g.preferred_purchase_date and g.purchase_required:
            # A preferred date is not a hard earliest date unless explicitly specified in hard constraints.
            pass
        base_day = day or g.as_of + timedelta(days=1)
        if method == "credit" and g.credit_debit_date and g.credit_debit_date < base_day:
            failures["credit_settlement_before_purchase"] += 1
            continue
        if purchase and (savings or invest):
            kind = "mixed"
            allocation = Allocation(purchase=purchase, savings=savings, investment=invest, cash=0)
        elif purchase:
            kind = "purchase_now"
            allocation = None
        elif savings:
            kind = "virtual_savings"
            allocation = None
        elif invest:
            kind = "virtual_investment"
            allocation = None
        else:
            kind = "no_purchase"
            allocation = None
        # Current stage-3 engine supports explicit expense adjustments, but not by category.
        # Until a category-preserving financial event exists, do not represent a fake refund.
        if reduction:
            failures["category_adjustment_engine_not_supported"] += 1
            continue
        budget = purchase + savings + invest if allocation is not None else (purchase or savings or invest)
        payload = WorldCompareRequest(
            user_id=g.user_id, as_of=g.as_of, horizon_days=req.horizon_days,
            purchase_amount=budget, purchase_date=base_day,
            payment_method=method, credit_debit_date=g.credit_debit_date if method == "credit" else None,
            savings_amount=savings, savings_date=base_day,
            investment_amount=invest, minimum_required_balance=g.minimum_balance or 0,
            allocation=allocation, stress=req.stress, income_delay_days=req.income_delay_days,
            daily_spend_increase_percent=req.daily_spend_increase_percent,
            investment_return_percent=req.investment_return_percent,
        )
        evaluated += 1
        try:
            result = compare_worlds(ds, payload)
            origin = result.baseline_source
        except (FinanceProblem, ValueError, KeyError) as exc:
            failures["financial_engine_error:" + str(exc)[:100]] += 1
            continue
        path = next((p for p in result.paths if p.scenario_id == kind), None)
        if path is None or path.status != "calculated":
            failures["unconfirmed_or_invalid_financial_path"] += 1
            continue
        checks = {
            "essential_bills": path.essential_payments_covered,
            "minimum_balance": path.minimum_balance >= (g.minimum_balance or 0),
            "purchase_requirement": not g.purchase_required or bool(path.purchase_completed),
            "required_savings": path.virtual_savings >= savings_min,
            "investment_permission": g.investment_allowed or invest == 0,
            "purchase_deadline": not day or not g.latest_purchase_date or day <= g.latest_purchase_date,
        }
        for failed, passed in checks.items():
            if not passed:
                failures[failed] += 1
        if not all(checks.values()):
            continue
        delay = max(0, (day - (g.preferred_purchase_date or g.as_of + timedelta(days=1))).days) if day else 0
        key = f"{purchase}:{day}:{method}:{savings}:{invest}:{reduction}:{path.minimum_balance}"
        if key in seen:
            continue
        seen.add(key)
        valid.append(Route(
            id=f"route-{len(valid)+1:03d}", label=_label(purchase, savings, invest, delay),
            kind=kind, purchase_date=day, payment_method=method,
            purchase_amount=purchase, savings_amount=savings, investment_amount=invest,
            purchase_delay_days=delay, minimum_balance=path.minimum_balance,
            ending_balance=path.daily_cashflow[-1].closing_balance if path.daily_cashflow else result.available_cash,
            savings_goal_impact=path.virtual_savings, investment_profit_loss=path.virtual_investment_profit_loss,
            investment_risk="hypothetical_loss_possible" if invest else "none",
            hard_constraints_met=True, checks=checks, scenario=path, engine_request=payload,
        ))
    frontier = [r for r in valid if not any(_dominates(other, r) for other in valid if other.id != r.id)]
    # Diverse families instead of selecting four scores from the same purchase date.
    picked: list[Route] = []
    for rank in ("purchase_now", "mixed", "virtual_savings", "no_purchase", "virtual_investment"):
        group = [x for x in frontier if x.kind == rank and x not in picked]
        if group and len(picked) < req.max_results:
            picked.append(sorted(group, key=lambda x: (x.purchase_delay_days, -x.minimum_balance))[0])
    for extra in sorted(frontier, key=lambda x: (-x.minimum_balance, x.purchase_delay_days)):
        if len(picked) >= req.max_results:
            break
        if extra not in picked:
            picked.append(extra)
    messages = []
    descriptions = {
        "essential_bills": "월세·카드대금 등 필수결제가 보호되지 않는 경로가 있어요.",
        "minimum_balance": "최저 잔액 조건을 만족할 수 없는 경로가 있어요.",
        "required_savings": "지정한 저축 금액을 유지하기 어려워요.",
        "purchase_deadline": "구매 기한이 현재 현금흐름과 충돌해요.",
        "purchase_requirement": "구매가 반드시 필요한 조건과 충돌해요.",
        "category_adjustment_engine_not_supported": "소비 카테고리 조정은 아직 검증 가능한 계산 이벤트가 없어 적용하지 않았어요.",
    }
    for reason, _ in failures.most_common(4):
        messages.append(descriptions.get(reason, "계산 엔진 검증에서 제외된 경로가 있어요. (" + reason + ")"))
    if not valid and not messages:
        messages = ["지정된 예산·결제일·저축 조건을 동시에 충족하는 경로를 발견하지 못했어요. 필수 조건은 임의로 완화하지 않았어요."]
    return SearchResult(
        user_id=g.user_id, as_of=g.as_of, dataset_id=ds.meta["dataset_id"],
        forecast_source=origin, status="ok" if picked else "no_valid_route",
        routes=picked, evaluated_count=evaluated, valid_count=len(valid),
        rejection_counts=dict(failures), conflict_explanations=messages if not picked else [],
        elapsed_ms=round((perf_counter()-started)*1000, 2),
    )

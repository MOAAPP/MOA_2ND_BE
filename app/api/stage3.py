"""Read-only 20-persona data bridge and stage-3 scenarios for Swagger/React."""
from __future__ import annotations
import json
import os
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.finance import dataset_dep
from app.core.settings import BACKEND_DIR, DEFAULT_DEMO_USER_ID
from app.schemas import CalculationRequest
from app.services.dataset import Dataset
from app.services.finance import FinanceProblem, calculate, state_for, kst
from app.services.stage3 import compare_worlds
from app.ml.forecast import run_forecast, to_events, ForecastProblem
from app.stage3_models import WorldCompareRequest, WorldCompareResult

router = APIRouter(prefix="/api/v4", tags=["v4-stage3-digital-twin"])
DEMO_USER_ID = DEFAULT_DEMO_USER_ID
STORAGE_DIR = Path(os.environ.get("MOA_STAGE3_STORAGE_DIR", str(BACKEND_DIR / "app/data/runtime/scenarios"))).resolve()


def _user(ds: Dataset, user_id: str):
    try:
        return ds.get_user(user_id)
    except KeyError as exc:
        raise HTTPException(404, {"code": "user_not_found", "user_id": user_id}) from exc


def _day(ds: Dataset, as_of: date | None):
    day = as_of or date.fromisoformat(ds.meta["snapshot_date"])
    begin = date.fromisoformat(ds.meta["start_date"])
    end = date.fromisoformat(ds.meta["snapshot_date"])
    if day < begin or day > end:
        raise HTTPException(422, {"code": "observation_date_out_of_range",
                                  "available_start": begin.isoformat(), "available_end": end.isoformat()})
    return day


def _observed(tx, day: date):
    if tx.get("future_scenario") or tx.get("synthetic_oracle_only") or tx.get("phase") == "future_scenario":
        return False
    if tx.get("status") not in ("confirmed_synthetic", "confirmed_user"):
        return False
    return tx.get("date", "9999") <= day.isoformat()


@router.get("/demo/default", summary="기본 시연 사용자와 실제 데이터셋 관측 범위")
def demo_default(ds: Dataset = Depends(dataset_dep)):
    if DEMO_USER_ID not in ds.profiles:
        raise HTTPException(503, {"code": "demo_user_missing", "user_id": DEMO_USER_ID})
    u = ds.get_user(DEMO_USER_ID)
    return {
        "user_id": u.user_id,
        "display_name": u.profile.get("description", u.user_id),
        "as_of_date": ds.meta["snapshot_date"],
        "observation_start": ds.meta["start_date"], "observation_end": ds.meta["snapshot_date"],
        "data_source": ds.meta["dataset_id"], "is_synthetic": True,
        "financial_state_status": "available",
        "user_count": len(ds.profiles),
    }


@router.get("/users/{user_id}/home", summary="메인 화면용 단일 사용자 금융 상태")
def user_home(user_id: str, as_of: date | None = Query(default=None),
              ds: Dataset = Depends(dataset_dep)):
    u = _user(ds, user_id)
    day = _day(ds, as_of)
    try:
        req = CalculationRequest(user_id=user_id, as_of=day, horizon_days=14)
        state = state_for(ds, req)
        try:
            variable_forecast = run_forecast(ds, user_id, day, 14)
        except ForecastProblem as exc:
            variable_forecast = {"model_type": "unavailable", "data_quality_status": "unavailable",
                                 "error": str(exc), "daily": [], "forecasts": []}
        # Never silently invent a model or subtract a cumulative forecast every day.
        if variable_forecast["model_type"] != "unavailable":
            req = req.model_copy(update={"forecast_events": to_events(u, variable_forecast)})
        result = calculate(ds, req)
    except FinanceProblem as exc:
        raise HTTPException(422, {"code": "invalid_financial_request", "message": str(exc)}) from exc
    observed = sorted(
        (dict(tx) for tx in u.transactions if _observed(tx, day)),
        key=lambda tx: (tx.get("occurred_at", 0), tx.get("id", "")), reverse=True)
    expected = [e.model_dump(mode="json") for row in result.daily_cashflow for e in row.events
                if e.transaction_status == "scheduled"]
    upcoming = sorted((e for e in expected if e["amount"] < 0 and e["essential"]),
                      key=lambda e: e["debit_at"])
    incoming = sorted((e for e in expected if e["amount"] > 0),
                      key=lambda e: e["debit_at"])
    observed_days = [d["date"] for d in u.daily if d.get("date", "") <= day.isoformat()
                     and d.get("observation_complete") and not d.get("future_scenario")]
    candidates = [c for c in u.purchase_intents if c.get("date", "") <= day.isoformat()]
    return {
        "user_id": user_id,
        "display_name": u.profile.get("description", user_id),
        "as_of_date": day, "observation_start": ds.meta["start_date"],
        "observation_end": ds.meta["snapshot_date"],
        "has_observation_on_date": day.isoformat() in observed_days,
        "data_source": ds.meta["dataset_id"], "is_synthetic": True,
        "financial_state_status": "available",
        "current_account_id": state.account_id,
        "available_cash": state.available_cash,
        "balance_snapshot_at": state.balance_snapshot_at,
        "balance_snapshot_source": state.balance_snapshot_source,
        "recent_transactions": observed[:12],
        "observed_transaction_count": len(observed),
        "upcoming_essential_payments": upcoming[:12],
        "upcoming_income": incoming[:12],
        "daily_cashflow": [d.model_dump(mode="json") for d in result.daily_cashflow],
        "future_balances": result.future_balances,
        "minimum_balance": result.minimum_balance,
        "savings_goals": state.savings_goals,
        "savings_goal_status": "not_in_canonical_dataset" if not state.savings_goals else "available",
        "virtual_investments": state.virtual_investments,
        "purchase_candidates": candidates[-5:],
        "warnings": result.warnings,
        "variable_forecast": variable_forecast,
    }


@router.get("/users/{user_id}/transactions", summary="기준일까지의 관측 확정 거래")
def user_transactions(user_id: str, as_of: date | None = None, limit: int = Query(100, ge=1, le=1000),
                      ds: Dataset = Depends(dataset_dep)):
    u = _user(ds, user_id)
    day = _day(ds, as_of)
    transactions = sorted((tx for tx in u.transactions if _observed(tx, day)),
                          key=lambda row: (row["date"], row.get("occurred_at", 0)), reverse=True)
    return {"user_id": user_id, "as_of_date": day, "total": len(transactions), "transactions": transactions[:limit]}


@router.get("/users/{user_id}/schedules", summary="사용자별 등록 금융 일정")
def user_schedules(user_id: str, as_of: date | None = None,
                   ds: Dataset = Depends(dataset_dep)):
    u = _user(ds, user_id)
    day = _day(ds, as_of)
    return {"user_id": user_id, "as_of_date": day,
            "schedules": list(u.schedules),
            "pending_card_liabilities": [x for x in u.pending_card_liabilities
                                         if not x.get("future_scenario") and x.get("due_date", "") >= day.isoformat()]}


@router.get("/instruments", summary="데이터셋의 가상 ETF/주식 테스트 시세")
def instruments(user_id: str = "S01", as_of: date | None = None, ds: Dataset = Depends(dataset_dep)):
    u = _user(ds, user_id)
    day = _day(ds, as_of)
    prices = {}
    for item in sorted(u.investments, key=lambda x: (x.get("date", ""), x.get("at", 0))):
        if not item.get("future_scenario") and item.get("date", "") <= day.isoformat():
            prices[item["code"]] = {"code": item["code"], "name": item["name"],
                                    "price": item["price"], "price_date": item["date"],
                                    "price_source": "fabricated_historical_test_not_live"}
    return {"user_id": user_id, "prices": list(prices.values()), "is_live": False}


@router.post("/scenarios/compare", response_model=WorldCompareResult,
             summary="동일 기준 상태에서 여섯 평행세계 계산")
def compare(payload: WorldCompareRequest, ds: Dataset = Depends(dataset_dep)):
    _user(ds, payload.user_id)
    try:
        return compare_worlds(ds, payload)
    except (FinanceProblem, ValueError) as exc:
        raise HTTPException(422, {"code": "scenario_invalid", "message": str(exc)}) from exc


class ScenarioSaveRequest(BaseModel):
    request: WorldCompareRequest
    label: str = Field(default="저장한 가상 선택", min_length=1, max_length=80)


@router.post("/scenarios/save", summary="원본 데이터셋과 분리된 가상 선택 저장")
def save_scenario(payload: ScenarioSaveRequest, ds: Dataset = Depends(dataset_dep)):
    result = compare(payload.request, ds)
    identifier = uuid4().hex
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    target = STORAGE_DIR / (identifier + ".json")
    tmp = STORAGE_DIR / (identifier + ".tmp")
    record = {
        "id": identifier, "label": payload.label, "user_id": payload.request.user_id,
        "dataset_id": ds.meta["dataset_id"], "saved_at": datetime.now().astimezone().isoformat(),
        "request": payload.request.model_dump(mode="json"),
        "result_summary": [{"scenario_id": p.scenario_id, "minimum_balance": p.minimum_balance,
                            "status": p.status} for p in result.paths],
        # Freeze the original computed paths; later observations never rewrite this result.
        "result_snapshot": result.model_dump(mode="json"),
        "forecast_snapshot": run_forecast(ds, payload.request.user_id,
                                           result.as_of, payload.request.horizon_days),
    }
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return {"id": identifier, "user_id": payload.request.user_id, "saved": True}


@router.get("/scenarios/{scenario_id}", summary="저장한 가상 선택 조회")
def get_saved_scenario(scenario_id: str, ds: Dataset = Depends(dataset_dep)):
    if len(scenario_id) != 32 or any(c not in "0123456789abcdef" for c in scenario_id):
        raise HTTPException(404, {"code": "scenario_not_found"})
    source = STORAGE_DIR / (scenario_id + ".json")
    if not source.is_file():
        raise HTTPException(404, {"code": "scenario_not_found"})
    record = json.loads(source.read_text(encoding="utf-8"))
    if record["dataset_id"] != ds.meta["dataset_id"]:
        raise HTTPException(409, {"code": "scenario_dataset_version_changed"})
    return record


@router.get("/ml/forecast-context", summary="4단계 ML용 관측 특징 입력 (미래 정답 제외)")
def ml_context(user_id: str = "S01", as_of: date | None = None,
               ds: Dataset = Depends(dataset_dep)):
    u = _user(ds, user_id)
    day = _day(ds, as_of)
    observations = [d for d in u.daily if d.get("date", "") <= day.isoformat()
                    and d.get("observation_complete") and not d.get("future_scenario")]
    return {
        "user_id": user_id, "as_of_date": day, "source": "synthetic_observation",
        "is_trained_ml_output": False, "prediction_contract": {
            "user_id": "string", "as_of": "YYYY-MM-DD",
            "forecast_run_id": "string", "daily_predicted_cash_outflow": "integer >= 0",
            "debit_date": "YYYY-MM-DD", "model_version": "string"},
        "recent_observed_variable_expenses": [
            {"date": d["date"], "observed_variable_spend": d["observed_variable_spend"]}
            for d in observations[-14:]],
    }

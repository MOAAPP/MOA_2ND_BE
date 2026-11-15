"""Frontend-ready variable-spend forecast series and offline model evaluation (Swagger-testable)."""
from __future__ import annotations
import json
import math
from datetime import date, timedelta
from datetime import date as Day
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

import app.ml.forecast as fc
from app.api.finance import dataset_dep
from app.api.ml import _view
from app.services.dataset import Dataset

router = APIRouter(prefix="/api/v4", tags=["v4-ml-forecast"])
Method = Literal["lightgbm", "overall", "recent7", "weekday"]
LABELS = {"lightgbm": "LightGBM", "overall": "전체 평균", "recent7": "최근 7일 이동평균", "weekday": "요일별 평균"}


class ChartPoint(BaseModel):
    date: Day = Field(description="YYYY-MM-DD (KST 결제계좌 출금일)")
    phase: Literal["history", "forecast"]
    predicted: int | None = Field(description="선택한 모델의 일 변동 현금유출 예측(원). history 구간은 null")
    actual: int | None = Field(description="관측 완료일의 실제 유출(원). 미관측/미래는 null (0원과 구분)")
    method: str | None = Field(default=None, description="predicted를 만든 방식. LightGBM 14일 초과분은 weekday_tail")
    lightgbm: int | None = None
    baseline_overall: int | None = None
    baseline_recent7: int | None = None
    baseline_weekday: int | None = None


class HorizonTotal(BaseModel):
    horizon_days: int
    predicted_total: int
    lightgbm_total: int | None
    baseline_overall_total: int
    baseline_recent7_total: int
    baseline_weekday_total: int
    actual_total: int | None = Field(description="해당 기간 전체가 관측된 경우에만 값이 있음")


class LiveMetric(BaseModel):
    model: str
    label: str
    days_compared: int
    mae: float | None
    rmse: float | None


class ForecastChartResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "user_id": "S01", "as_of": "2026-11-04", "horizon_days": 7, "model": "lightgbm",
        "model_version": "lgbm-cashoutflow-v5.0", "forecast_run_id": "0123456789abcdef01234567",
        "data_quality_status": "complete", "serving_default_model": "overall",
        "series": [{"date": "2026-11-04", "phase": "history", "predicted": None, "actual": 21000, "method": None,
                    "lightgbm": None, "baseline_overall": None, "baseline_recent7": None, "baseline_weekday": None},
                   {"date": "2026-11-05", "phase": "forecast", "predicted": 19000, "actual": None, "method": "lightgbm",
                    "lightgbm": 19000, "baseline_overall": 21000, "baseline_recent7": 20500, "baseline_weekday": 18000}],
        "totals": [], "live_metrics": [], "warnings": ["synthetic_dataset_not_real_user_validated"]}]})
    user_id: str
    as_of: date
    horizon_days: int
    model: Method
    model_version: str
    forecast_run_id: str
    data_quality_status: str
    serving_default_model: str | None = Field(description="검증 구간 7일 MAE로 선택된 서비스 기본 모델")
    series: list[ChartPoint]
    totals: list[HorizonTotal]
    live_metrics: list[LiveMetric] = Field(description="이번 요청에서 실제값이 있는 예측일에 대한 모델별 일 단위 오차")
    warnings: list[str]


def _metrics(pairs):
    if not pairs:
        return None, None
    errs = [p - a for p, a in pairs]
    return round(sum(abs(e) for e in errs) / len(errs), 2), round(math.sqrt(sum(e * e for e in errs) / len(errs)), 2)


@router.get("/users/{user_id}/forecast-chart", response_model=ForecastChartResponse,
            summary="그래프용 날짜별 변동지출 예측 (예측값 + 실제값 + 베이스라인)",
            description="history(최근 실제값) + forecast(예측·베이스라인·실제) 배열. LightGBM을 실제로 호출하며 "
                        "model=lightgbm이 기본입니다. 기준일 이후에 관측된 날은 actual도 함께 제공해 백테스트 그래프로 쓸 수 있습니다.")
def forecast_chart(
    user_id: str = Path(..., description="S01~S20", examples=["S01"]),
    as_of: date | None = Query(None, description="예측 기준일. 비우면 데이터 기준일(2026-11-04)", examples=["2026-11-04"]),
    horizon_days: int = Query(14, ge=3, le=90, description="예측 일수", examples=[14]),
    model: Method = Query("lightgbm", description="predicted에 사용할 모델", examples=["lightgbm"]),
    lookback_days: int = Query(7, ge=0, le=30, description="앞에 붙일 최근 실제값 일수"),
    ds: Dataset = Depends(dataset_dep),
):
    if user_id not in ds.profiles:
        raise HTTPException(404, {"code": "user_not_found", "user_id": user_id})
    snapshot = date.fromisoformat(ds.meta["snapshot_date"])
    day = as_of or snapshot
    view = _view(ds, user_id, day)
    u = ds.get_user(user_id)
    try:
        main = fc.run_forecast(view, user_id, day, horizon_days, model_override=model)
    except fc.ForecastProblem as exc:
        raise HTTPException(422, {"code": "forecast_unavailable", "message": str(exc)}) from exc
    if main["model_type"] == "unavailable":
        raise HTTPException(422, {"code": "not_enough_complete_observations", "message": "관측 완료일이 부족합니다."})
    warnings = ["synthetic_dataset_not_real_user_validated"]
    lgbm = main
    if model != "lightgbm":
        try:
            lgbm = fc.run_forecast(view, user_id, day, horizon_days, model_override="lightgbm")
        except fc.ForecastProblem as exc:
            lgbm = None
            warnings.append(f"lightgbm_column_unavailable:{exc}")
    past = fc.observed_ledger(u, day)            # inputs for baselines: nothing after as_of
    truth = fc.observed_ledger(u, snapshot)      # observed actuals only; oracle future rows are never included
    def actual(d):
        row = truth.get(d)
        return None if row is None or row["outflow"] is None else int(row["outflow"])
    series: list[ChartPoint] = []
    for i in range(lookback_days - 1, -1, -1):
        d = day - timedelta(days=i)
        series.append(ChartPoint(date=d, phase="history", predicted=None, actual=actual(d)))
    base = {"overall": [], "recent7": [], "weekday": []}
    for idx, item in enumerate(main["daily"]):
        d = date.fromisoformat(item["date"])
        vals = {m: max(0, round(fc.baseline_daily(past, day, d, m))) for m in base}
        for m in base:
            base[m].append(vals[m])
        series.append(ChartPoint(
            date=d, phase="forecast", predicted=item["amount"], actual=actual(d), method=item["method"],
            lightgbm=lgbm["daily"][idx]["amount"] if lgbm else None,
            baseline_overall=vals["overall"], baseline_recent7=vals["recent7"], baseline_weekday=vals["weekday"]))
    forecast_rows = [p for p in series if p.phase == "forecast"]
    totals = []
    for n in (3, 7, 14):
        if n > horizon_days:
            continue
        rows = forecast_rows[:n]
        known = [p.actual for p in rows]
        totals.append(HorizonTotal(
            horizon_days=n, predicted_total=sum(p.predicted for p in rows),
            lightgbm_total=sum(p.lightgbm for p in rows) if lgbm else None,
            baseline_overall_total=sum(p.baseline_overall for p in rows),
            baseline_recent7_total=sum(p.baseline_recent7 for p in rows),
            baseline_weekday_total=sum(p.baseline_weekday for p in rows),
            actual_total=sum(known) if all(k is not None for k in known) else None))
    live = []
    for name, getter in (("lightgbm", lambda p: p.lightgbm), ("overall", lambda p: p.baseline_overall),
                         ("recent7", lambda p: p.baseline_recent7), ("weekday", lambda p: p.baseline_weekday)):
        pairs = [(getter(p), p.actual) for p in forecast_rows if p.actual is not None and getter(p) is not None]
        mae, rmse = _metrics(pairs)
        live.append(LiveMetric(model=name, label=LABELS[name], days_compared=len(pairs), mae=mae, rmse=rmse))
    if not any(m.days_compared for m in live):
        warnings.append("no_observed_actuals_in_forecast_window_actual_is_null")
    try:
        default_model = json.loads((fc.MODEL_DIR / "metadata.json").read_text(encoding="utf-8")).get("selected_model")
    except (OSError, ValueError):
        default_model = None
    return ForecastChartResponse(
        user_id=user_id, as_of=day, horizon_days=horizon_days, model=model, model_version=main["model_version"],
        forecast_run_id=main["forecast_run_id"], data_quality_status=main["data_quality_status"],
        serving_default_model=default_model, series=series, totals=totals, live_metrics=live, warnings=warnings)


class MetricRow(BaseModel):
    method: str
    label: str
    horizon_days: int
    cases: int
    mae: float | None
    rmse: float | None
    bias: float | None = Field(description="예측-실제 합계 평균. 음수=과소예측")
    daily_mae: float | None
    daily_rmse: float | None
    balance_mae_proxy: float | None = None
    missed_shortfalls_proxy: int = 0
    false_warnings_proxy: int = 0


class EvaluationResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "metadata": {"version": "lgbm-cashoutflow-v5.0", "selected_model": "overall"},
        "metrics": {"validation": [{"method": "lightgbm", "label": "LightGBM", "horizon_days": 7, "cases": 137,
                                    "mae": 47019.94, "rmse": 72929.32, "bias": -34560.98, "daily_mae": 11269.18,
                                    "daily_rmse": 20401.18}], "test": []},
        "error_analysis": {"horizon_days": 7, "by_weekday": [], "by_spend_level": [], "worst_cases_lightgbm": []},
        "warnings": ["synthetic evaluation never demonstrates real-user performance"]}]})
    metadata: dict
    metrics: dict[str, list[MetricRow]]
    error_analysis: dict
    warnings: list[str]


@router.get("/ml/evaluation", response_model=EvaluationResponse,
            summary="오프라인 모델 성능 비교표 (LightGBM vs 전체평균/최근7일/요일평균) + 오류 분석",
            description="`python -m app.ml.train`이 저장한 evaluation.json을 그대로 반환합니다. 요청 시 재학습하지 않습니다.")
def evaluation():
    path = fc.MODEL_DIR / "evaluation.json"
    if not path.is_file():
        raise HTTPException(404, {"code": "evaluation_not_found", "message": "먼저 python -m app.ml.train 을 실행하세요."})
    data = json.loads(path.read_text(encoding="utf-8"))
    if "metrics" not in data:
        raise HTTPException(409, {"code": "evaluation_outdated", "message": "구버전 평가 파일입니다. 다시 학습하세요."})
    return {k: data[k] for k in ("metadata", "metrics", "error_analysis", "warnings")}

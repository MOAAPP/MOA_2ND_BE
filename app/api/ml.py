"""Swagger-visible inference and deterministic cashflow overlay. Training is CLI-only."""
from datetime import date
from datetime import date as Day
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Path,Query
from pydantic import BaseModel,ConfigDict,Field
from app.api.finance import dataset_dep
from app.schemas import CalculationRequest
from app.services.dataset import Dataset
from app.services.observed_view import for_user_date

def _view(ds, uid, when):
    try:
        return for_user_date(ds, uid, when)
    except KeyError as exc:
        raise HTTPException(404, {"code": "user_not_found"}) from exc
    except ValueError as exc:
        raise HTTPException(422, {"code": "date_invalid", "message": str(exc)}) from exc
from app.services.finance import calculate,FinanceProblem
from app.ml.forecast import ForecastProblem,run_forecast,to_events

router=APIRouter(prefix="/api/v4",tags=["v4-ml-forecast"])

class DailyForecast(BaseModel):
    date:Day=Field(description="YYYY-MM-DD 출금일")
    amount:int=Field(ge=0,description="예측 변동 현금유출(원)")
    method:str=Field(description="lightgbm/overall/recent7/weekday/weekday_tail")

class HorizonForecast(BaseModel):
    horizon_days:Literal[3,7,14]
    predicted_variable_outflow:int

class ForecastResponse(BaseModel):
    model_config=ConfigDict(extra="allow",json_schema_extra={"examples":[{
        "user_id":"S01","forecast_as_of":"2026-11-04","forecast_run_id":"309c4409bbc53bba8950e41f",
        "model_type":"lightgbm","model_version":"lgbm-cashoutflow-v5.0","training_data_cutoff":"2026-10-05",
        "data_quality_status":"complete","observation_start":"2026-09-11","observation_end":"2026-11-04",
        "observed_days":55,"horizon_days":14,
        "daily":[{"date":"2026-11-05","amount":21811,"method":"lightgbm"}],
        "forecasts":[{"horizon_days":3,"predicted_variable_outflow":76350}],
        "upper_scenario":{"kind":"assumption_not_calibrated","increase_percent":25,"daily":[]},
        "is_synthetic":True,"candidate_purchases_included":False}]})
    user_id:str
    forecast_as_of:date
    forecast_run_id:str
    model_type:str
    model_version:str
    training_data_cutoff:str|None=None
    data_quality_status:str
    observation_start:date
    observation_end:date
    observed_days:int
    horizon_days:int
    daily:list[DailyForecast]
    forecasts:list[HorizonForecast]
    upper_scenario:dict
    is_synthetic:bool
    candidate_purchases_included:bool

def forecast_or_error(ds,user_id,as_of,horizon,model=None):
    if user_id not in ds.profiles:
        raise HTTPException(404,{"code":"user_not_found","user_id":user_id})
    as_of=as_of or date.fromisoformat(ds.meta["snapshot_date"])
    try:
        return run_forecast(_view(ds,user_id,as_of),user_id,as_of,horizon,model_override=model)
    except ForecastProblem as exc:
        raise HTTPException(422,{"code":"forecast_unavailable","message":str(exc)}) from exc

@router.get("/users/{user_id}/forecast",response_model=ForecastResponse,summary="학습 모델/검증 기준선 미래 3·7·14일 변동 현금유출")
def forecast(user_id:str=Path(...,examples=["S01"]),as_of:date|None=Query(None,examples=["2026-11-04"]),horizon_days:int=Query(14,ge=14,le=90,examples=[14]),
             model:str|None=Query(None,pattern="^(lightgbm|overall|recent7|weekday)$",
                                  description="비우면 검증 성능으로 선택된 서비스 모델. 지정하면 해당 모델로 예측"),
             ds:Dataset=Depends(dataset_dep)):
    return forecast_or_error(ds,user_id,as_of,horizon_days,model)

@router.get("/users/{user_id}/forecast-cashflow",summary="확정 일정 + 변동지출 전망 + 급여 지연 별도 시나리오")
def forecast_cashflow(user_id:str,as_of:date|None=None,horizon_days:int=Query(14,ge=14,le=90),
                      scenario:str=Query("base",pattern="^(base|spending_high|income_delay|combined)$"),
                      income_delay_days:int=Query(3,ge=0,le=31),ds:Dataset=Depends(dataset_dep)):
    ds=_view(ds,user_id,as_of)
    model=forecast_or_error(ds,user_id,as_of,horizon_days)
    if model["model_type"]=="unavailable":
        raise HTTPException(422,{"code":"not_enough_complete_observations"})
    high=scenario in ("spending_high","combined")
    delay=scenario in ("income_delay","combined")
    req=CalculationRequest(user_id=user_id,as_of=as_of or date.fromisoformat(ds.meta["snapshot_date"]),
                           horizon_days=horizon_days,forecast_events=to_events(ds.get_user(user_id),model,upper=high),
                           income_delay_days=income_delay_days if delay else 0)
    try:
        result=calculate(ds,req)
    except FinanceProblem as exc:
        raise HTTPException(422,{"code":"cashflow_invalid","message":str(exc)}) from exc
    return {"forecast":model,"scenario":scenario,"scenario_status":"assumption_not_calibrated" if high or delay else "model_or_baseline",
            "cashflow":result.model_dump(mode="json")}

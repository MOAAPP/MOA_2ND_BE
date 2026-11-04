"""Swagger-visible inference and deterministic cashflow overlay. Training is CLI-only."""
from datetime import date
from fastapi import APIRouter,Depends,HTTPException,Query
from app.api.finance import dataset_dep
from app.schemas import CalculationRequest
from app.services.dataset import Dataset
from app.services.finance import calculate,FinanceProblem
from app.ml.forecast import ForecastProblem,run_forecast,to_events

router=APIRouter(prefix="/api/v4",tags=["v4-ml-forecast"])

def forecast_or_error(ds,user_id,as_of,horizon):
    if user_id not in ds.profiles:
        raise HTTPException(404,{"code":"user_not_found","user_id":user_id})
    as_of=as_of or date.fromisoformat(ds.meta["snapshot_date"])
    try:
        return run_forecast(ds,user_id,as_of,horizon)
    except ForecastProblem as exc:
        raise HTTPException(422,{"code":"forecast_unavailable","message":str(exc)}) from exc

@router.get("/users/{user_id}/forecast",summary="학습 모델/검증 기준선 미래 3·7·14일 변동 현금유출")
def forecast(user_id:str,as_of:date|None=None,horizon_days:int=Query(14,ge=14,le=90),
             ds:Dataset=Depends(dataset_dep)):
    return forecast_or_error(ds,user_id,as_of,horizon_days)

@router.get("/users/{user_id}/forecast-cashflow",summary="확정 일정 + 변동지출 전망 + 급여 지연 별도 시나리오")
def forecast_cashflow(user_id:str,as_of:date|None=None,horizon_days:int=Query(14,ge=14,le=90),
                      scenario:str=Query("base",pattern="^(base|spending_high|income_delay|combined)$"),
                      income_delay_days:int=Query(3,ge=0,le=31),ds:Dataset=Depends(dataset_dep)):
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

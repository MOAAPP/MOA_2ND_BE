"""Stage 6 personal briefing and plan tracking API (synthetic demo only)."""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from app.api.finance import dataset_dep
from app.services.dataset import Dataset
from app.services.finance import FinanceProblem
from app.services.stage6 import briefing, spend_capacity, list_saved_plans, track_plan
from app.services.ai_briefing import ai_briefing

router = APIRouter(prefix="/api/v4", tags=["v4-stage6-personalized"])


def _handle(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, {"code": "user_or_plan_not_found", "message": str(exc)}) from exc
    except (FinanceProblem, ValueError) as exc:
        raise HTTPException(422, {"code": "invalid_analysis_request", "message": str(exc)}) from exc


@router.get("/users/{user_id}/spending-capacity", summary="결제일별 최소잔액을 지키는 오늘의 추정 추가 소비 여유")
def spending_capacity(user_id: str, as_of: date | None = None,
                      horizon_days: int = Query(14, ge=1, le=90),
                      minimum_required_balance: int = Query(0, ge=0),
                      income_delay_days: int = Query(0, ge=0, le=31),
                      payment_method: str = Query("debit", pattern="^(debit|credit)$"),
                      credit_debit_date: date | None = None,
                      ds: Dataset = Depends(dataset_dep)):
    return _handle(spend_capacity, ds, user_id, as_of, horizon=horizon_days,
                   minimum_balance=minimum_required_balance, income_delay_days=income_delay_days,
                   payment_method=payment_method, credit_debit_date=credit_debit_date)


@router.get("/users/{user_id}/briefing", summary="Python 금융 사실 + 설정된 경우 실제 LLM 개인화 설명")
def morning_briefing(user_id: str, as_of: date | None = None,
                     minimum_required_balance: int = Query(0, ge=0),
                     ds: Dataset = Depends(dataset_dep)):
    return _handle(ai_briefing, ds, user_id, as_of, minimum_balance=minimum_required_balance)


@router.get("/users/{user_id}/changes", summary="지난 분석 대비 금융 변화 사건")
def financial_changes(user_id: str, as_of: date | None = None,
                      minimum_required_balance: int = Query(0, ge=0),
                      ds: Dataset = Depends(dataset_dep)):
    data = _handle(briefing, ds, user_id, as_of, minimum_balance=minimum_required_balance)
    return {"user_id": user_id, "as_of_date": data["as_of_date"],
            **data["change_detection"]}


@router.get("/users/{user_id}/plans", summary="사용자별 저장 경로 목록")
def saved_plans(user_id: str, ds: Dataset = Depends(dataset_dep)):
    return _handle(list_saved_plans, ds, user_id)


@router.get("/users/{user_id}/plans/{plan_id}/tracking", summary="저장 분석 스냅샷과 이후 관측 지출 비교")
def plan_tracking(user_id: str, plan_id: str, as_of: date | None = None,
                  ds: Dataset = Depends(dataset_dep)):
    return _handle(track_plan, ds, user_id, plan_id, as_of)


@router.get("/users/{user_id}/assets/news", summary="관련 뉴스 제공 상태 (확인 가능한 출처만)")
def asset_news(user_id: str, ds: Dataset = Depends(dataset_dep)):
    if user_id not in ds.profiles:
        raise HTTPException(404, {"code": "user_not_found"})
    return {"user_id": user_id, "status": "provider_not_configured",
            "articles": [], "is_real_news": False,
            "message": "연결된 실제 뉴스 출처가 없어 뉴스를 표시하지 않아요."}

"""Private-data-minimizing preview API: anonymous numeric inputs, no persistence."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request

from app.personal_models import (
    PersonalCompareRequest, PersonalCompareResult,
    PersonalGoalRequest, PersonalGoalResult,
)
from app.services.personal_cashflow import compare_personal, analyze_goal

router = APIRouter(prefix="/api/v4/personal", tags=["v4-personal-preview"])


def _preview_guard(request: Request):
    """Reject public production uploads if the upstream gateway isn't secured.

    The real product will require authenticated per-user storage and consent.
    This route is deliberately stateless. In tests/local dev, no server secret
    is needed; production requires MOA_REQUIRE_GATEWAY=1 and a gateway key.
    """
    is_production = (os.environ.get("MOA_DEPLOYMENT_MODE", "").lower() == "production"
                     or os.environ.get("VERCEL_ENV", "").lower() == "production")
    secured = os.environ.get("MOA_REQUIRE_GATEWAY", "").lower() in ("1", "true", "yes")
    if is_production and (not secured or not os.environ.get("MOA_GATEWAY_SECRET")):
        raise HTTPException(status_code=503, detail={
            "code": "personal_preview_requires_secure_gateway",
            "message": "사용자 금융 데이터를 받으려면 운영 게이트웨이 보호가 필요합니다.",
        })


@router.post("/compare", response_model=PersonalCompareResult,
             summary="직접 입력한 잔액·월급·필수 결제·예산으로 6가지 7~90일 평행세계 비교 (저장 안 함)")
def personal_compare(payload: PersonalCompareRequest, request: Request):
    _preview_guard(request)
    return compare_personal(payload)


@router.post("/goal-analyze", response_model=PersonalGoalResult,
             summary="내 금융 입력값으로 구매·저축·투자 경로의 안전성 규칙 검증 (LLM 미사용)")
def personal_goal(payload: PersonalGoalRequest, request: Request):
    _preview_guard(request)
    return analyze_goal(payload)

"""Swagger endpoints for stage-5 tool calling and deterministic route search."""
from __future__ import annotations
import json
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.finance import dataset_dep
from app.core.settings import BACKEND_DIR
from app.services.dataset import Dataset
from app.services.agent_paths import GoalConditions, SearchRequest, SearchResult, search_paths
from app.services.agent_llm import AgentFailure, run_agent
from app.services.finance import FinanceProblem

router = APIRouter(prefix="/api/v4/agent", tags=["v4-stage5-agent"])
SAVE_DIR = BACKEND_DIR / "app/data/runtime/agent_routes"


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(pattern=r"^S\d\d$")
    as_of: date
    goal: str = Field(min_length=3, max_length=1200)
    overrides: dict = Field(default_factory=dict)
    stress: str = Field(default="baseline", pattern="^(baseline|higher_spending|delayed_income|combined|investment_loss)$")
    max_evaluations: int = Field(default=72, ge=1, le=150)


class SaveRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search_request: SearchRequest
    route_id: str = Field(min_length=5, max_length=80)
    label: str = Field(default="내가 고른 금융 경로", min_length=1, max_length=80)


@router.post("/analyze", summary="LLM 조건 추출 → 실제 Python Tool Calling → 검증된 경로 탐색")
def analyze(payload: AnalyzeRequest, ds: Dataset = Depends(dataset_dep)):
    if payload.user_id not in ds.profiles:
        raise HTTPException(404, {"code": "user_not_found", "user_id": payload.user_id})
    try:
        return run_agent(ds, payload.user_id, payload.as_of, payload.goal, payload.overrides,
                         payload.stress, payload.max_evaluations)
    except AgentFailure as exc:
        raise HTTPException(503, {"code": "llm_unavailable", "message": str(exc),
                                  "basic_parallel_worlds_unchanged": True}) from exc
    except (ValueError, FinanceProblem) as exc:
        raise HTTPException(422, {"code": "goal_or_finance_invalid", "message": str(exc)}) from exc
    except Exception as exc:
        # Do not leak provider credentials or personal data in HTTP response.
        raise HTTPException(502, {"code": "llm_provider_error",
                                  "message": "AI 조건 분석에 실패했어요. 기존 여섯 평행세계는 계속 사용할 수 있어요."}) from exc


@router.post("/search", response_model=SearchResult, summary="LLM 없이 수정된 제약조건으로 Python 경로 재탐색")
def search(payload: SearchRequest, ds: Dataset = Depends(dataset_dep)):
    try:
        return search_paths(ds, payload)
    except KeyError as exc:
        raise HTTPException(404, {"code": "user_not_found"}) from exc
    except (ValueError, FinanceProblem) as exc:
        raise HTTPException(422, {"code": "constraint_invalid", "message": str(exc)}) from exc


@router.post("/routes/save", summary="검증된 에이전트 경로를 원본 데이터셋과 분리하여 저장")
def save_route(payload: SaveRouteRequest, ds: Dataset = Depends(dataset_dep)):
    try:
        result = search_paths(ds, payload.search_request)
    except (ValueError, KeyError, FinanceProblem) as exc:
        raise HTTPException(422, {"code": "route_revalidation_failed"}) from exc
    matched = next((r for r in result.routes if r.id == payload.route_id and r.hard_constraints_met), None)
    if matched is None:
        raise HTTPException(422, {"code": "route_not_valid_on_recalculation"})
    uid = payload.search_request.conditions.user_id
    identifier = uuid4().hex
    record = {
        "id": identifier, "user_id": uid, "dataset_id": ds.meta["dataset_id"],
        "as_of": payload.search_request.conditions.as_of.isoformat(),
        "label": payload.label, "saved_at": datetime.now(timezone.utc).isoformat(),
        "search_request": payload.search_request.model_dump(mode="json"),
        "route_identity": {"purchase_date": matched.purchase_date.isoformat() if matched.purchase_date else None,
                           "purchase_amount": matched.purchase_amount, "savings_amount": matched.savings_amount,
                           "investment_amount": matched.investment_amount,
                           "payment_method": matched.payment_method, "kind": matched.kind},
        "verified_minimum_balance": matched.minimum_balance,
    }
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SAVE_DIR / (identifier + ".tmp")
    dest = SAVE_DIR / (identifier + ".json")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(dest)
    return {"id": identifier, "user_id": uid, "saved": True, "saved_route": record["route_identity"]}


@router.get("/routes/{route_id}", summary="6단계 연계: 저장한 경로의 입력·기준 상태 조회")
def get_saved(route_id: str, user_id: str = Query(...), ds: Dataset = Depends(dataset_dep)):
    if len(route_id) != 32 or any(c not in "0123456789abcdef" for c in route_id):
        raise HTTPException(404, {"code": "route_not_found"})
    path = SAVE_DIR / (route_id + ".json")
    if not path.is_file():
        raise HTTPException(404, {"code": "route_not_found"})
    record = json.loads(path.read_text(encoding="utf-8"))
    if record["user_id"] != user_id:
        raise HTTPException(404, {"code": "route_not_found"})
    if record["dataset_id"] != ds.meta["dataset_id"]:
        raise HTTPException(409, {"code": "dataset_version_changed"})
    return record


@router.post("/routes/{route_id}/recalculate", summary="6단계 연계: 원본 기준 데이터로 선택 경로 다시 검증")
def recalculate(route_id: str, user_id: str = Query(...), ds: Dataset = Depends(dataset_dep)):
    record = get_saved(route_id, user_id, ds)
    request = SearchRequest.model_validate(record["search_request"])
    result = search_paths(ds, request)
    key = record["route_identity"]
    def identity(r):
        return {
            "purchase_date": r.purchase_date.isoformat() if r.purchase_date else None,
            "purchase_amount": r.purchase_amount, "savings_amount": r.savings_amount,
            "investment_amount": r.investment_amount,
            "payment_method": r.payment_method, "kind": r.kind,
        }
    matched = next((r for r in result.routes if identity(r) == key), None)
    return {"id": route_id, "user_id": user_id, "still_valid": bool(matched),
            "route": matched, "status": result.status,
            "revalidation_note": "현재 데이터셋과 동일 관측 기준일에 재계산했어요. 이후 실제 변동 관측은 별도 동기화가 필요해요."}


@router.get("/dataset/audit", summary="원본 데이터셋의 20명·참조·관측범위 검증")
def dataset_audit(ds: Dataset = Depends(dataset_dep)):
    collections = ("accounts", "transactions", "schedules", "balance_checks", "daily",
                   "pending_card_liabilities", "investments", "portfolio_snapshots", "purchase_intents")
    counts = {key: sum(len(getattr(user, key)) for user in ds.by_user.values())
              for key in collections}
    user_ids = sorted(ds.profiles)
    problems = []
    if len(user_ids) != 20:
        problems.append("expected_20_profiles_found_" + str(len(user_ids)))
    for uid, user in ds.by_user.items():
        if not user.accounts:
            problems.append(f"{uid}:missing_account")
        if not user.daily:
            problems.append(f"{uid}:missing_daily")
        if user.profile.get("payment_account_id") not in {a.get("account_id") for a in user.accounts}:
            problems.append(f"{uid}:payment_account_not_found")
    return {"dataset_id": ds.meta["dataset_id"], "synthetic": True,
            "user_count": len(user_ids), "user_ids": user_ids, "default_user_id": __import__("app.core.settings", fromlist=["DEFAULT_DEMO_USER_ID"]).DEFAULT_DEMO_USER_ID,
            "observation_start": ds.meta["start_date"], "observation_end": ds.meta["snapshot_date"],
            "collection_counts": counts, "problems": problems, "valid": not problems}

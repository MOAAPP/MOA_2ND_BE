"""AI status and per-user quiz; never expose API keys to the browser."""
from __future__ import annotations
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal
from app.api.finance import dataset_dep
from app.services.dataset import Dataset
from app.services.ai_provider import configured, provider_settings
from app.services.learning_ai import quiz_for, answer_quiz, learning_history, learning_overview, get_interests, set_interests
from app.services.state_store import storage_kind

router = APIRouter(prefix="/api/v4", tags=["v4-ai-learning"])

@router.get("/ai/status", summary="서버에 실제 AI 키 설정 여부만 조회 (키 내용 비공개)")
def ai_status():
    _, _, model = provider_settings()
    return {
        "llm_configured": configured(), "agent_configured": configured("agent"),
        "vision_configured": configured("vision"),
        "text_model": model if configured() else None,
        "data_source": "synthetic_20_users",
        "keys_exposed": False,
        "learning_storage": storage_kind(),
        "durable_storage_configured": storage_kind() == "private_blob" or not os.getenv("VERCEL"),
        "storage_verification_endpoint": "/api/v4/learning/storage-check",
        "identity_mode": "synthetic_demo_users",
    }

class AnswerInput(BaseModel):
    quiz_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    choice: int = Field(ge=0, le=3)

class InterestsInput(BaseModel):
    topics: list[Literal["cashflow", "credit", "saving", "investing", "forecast"]] = Field(max_length=5)

def _day(day: date | None) -> date:
    return day or datetime.now(ZoneInfo("Asia/Seoul")).date()

@router.get("/users/{user_id}/learning-interests", summary="금융 학습 관심사 조회")
def read_interests(user_id: str, day: date | None = None, ds: Dataset = Depends(dataset_dep)):
    try:
        return {"user_id": user_id, "topics": get_interests(ds, user_id, _day(day))}
    except KeyError:
        raise HTTPException(404, {"code": "user_not_found"})

@router.post("/users/{user_id}/learning-interests", summary="금융 학습 관심사 설정")
def update_interests(user_id: str, payload: InterestsInput, day: date | None = None, ds: Dataset = Depends(dataset_dep)):
    try:
        chosen = _day(day)
        if chosen < date.fromisoformat(ds.meta['start_date']) or chosen > _day(None):
            raise ValueError('quiz_date_not_available')
        return set_interests(ds, user_id, payload.topics, chosen)
    except KeyError:
        raise HTTPException(404, {"code": "user_not_found"})
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid_interests", "message": str(exc)})

@router.get("/users/{user_id}/daily-quiz", summary="최근 오답·금융 기록을 활용한 개인별 1일 1문제")
def get_quiz(user_id: str, day: date | None = Query(default=None),
             ds: Dataset = Depends(dataset_dep)):
    try:
        return quiz_for(ds, user_id, _day(day))
    except KeyError:
        raise HTTPException(404, {"code": "user_not_found"})
    except ValueError as exc:
        if str(exc) == "quiz_generation_in_progress":
            raise HTTPException(409, {"code":"quiz_generation_in_progress","message":"문제를 생성하고 있어요. 잠시 후 다시 불러와 주세요."})
        raise HTTPException(422, {"code": "quiz_invalid", "message": str(exc)})

@router.get("/users/{user_id}/learning-history", summary="최근 퀴즈 90개 중 답변한 학습 이력과 정답·오답 집계 조회")
def get_learning_history(user_id: str, day: date | None = Query(default=None),
                         ds: Dataset = Depends(dataset_dep)):
    try:
        return learning_history(ds, user_id, _day(day))
    except KeyError:
        raise HTTPException(404, {"code": "user_not_found"})
    except ValueError as exc:
        if str(exc) == "quiz_generation_in_progress":
            raise HTTPException(409, {"code":"quiz_generation_in_progress","message":"문제를 생성하고 있어요. 잠시 후 다시 불러와 주세요."})
        raise HTTPException(422, {"code": "quiz_invalid", "message": str(exc)})

@router.post("/users/{user_id}/daily-quiz/answer", summary="하루 한 번 답변 저장, 정답·해설 반환")
def submit_answer(user_id: str, payload: AnswerInput,
                  day: date | None = Query(default=None), ds: Dataset = Depends(dataset_dep)):
    try:
        return answer_quiz(ds, user_id, _day(day), payload.quiz_id, payload.choice)
    except KeyError:
        raise HTTPException(404, {"code": "user_not_found"})
    except ValueError as exc:
        raise HTTPException(409 if str(exc) == "quiz_already_answered" else 422,
                            {"code": "quiz_invalid", "message": str(exc)})


class TopicProgress(BaseModel):
    topic: Literal["cashflow", "credit", "saving", "investing", "forecast"]
    attempt_count: int
    correct_count: int
    needs_review: bool
    next_review_date: date | None
    guide: str

class LearningOverviewResponse(BaseModel):
    user_id: str
    as_of: date
    source: Literal["stored_answers"]
    history_quiz_limit: int
    streak_days: int
    attempt_count: int
    correct_count: int
    topics: list[TopicProgress]
    review_topic_count: int

@router.get("/users/{user_id}/learning-overview", response_model=LearningOverviewResponse,
            summary="저장된 답안 기반 주제별 학습 현황·미해결 복습·연속 학습일")
def get_overview(user_id: str, day: date | None = Query(default=None), ds: Dataset = Depends(dataset_dep)):
    try:
        return learning_overview(ds, user_id, _day(day))
    except KeyError:
        raise HTTPException(404, {"code": "user_not_found"})
    except ValueError as exc:
        raise HTTPException(422, {"code": "learning_date_invalid", "message": str(exc)})

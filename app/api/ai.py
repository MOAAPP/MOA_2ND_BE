"""AI status and per-user quiz; never expose API keys to the browser."""
from __future__ import annotations
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.api.finance import dataset_dep
from app.services.dataset import Dataset
from app.services.ai_provider import configured, provider_settings
from app.services.learning_ai import quiz_for, answer_quiz

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
    }

class AnswerInput(BaseModel):
    quiz_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    choice: int = Field(ge=0, le=3)

def _day(day: date | None) -> date:
    return day or datetime.now(ZoneInfo("Asia/Seoul")).date()

@router.get("/users/{user_id}/daily-quiz", summary="최근 오답·금융 기록을 활용한 개인별 1일 1문제")
def get_quiz(user_id: str, day: date | None = Query(default=None),
             ds: Dataset = Depends(dataset_dep)):
    try:
        return quiz_for(ds, user_id, _day(day))
    except KeyError:
        raise HTTPException(404, {"code": "user_not_found"})
    except ValueError as exc:
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

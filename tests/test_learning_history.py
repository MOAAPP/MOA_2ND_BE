from datetime import date

from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app
from app.services.learning_ai import _topic


def test_unresolved_older_topic_is_not_hidden_by_corrected_topic():
    rows = [
        {"topic": "saving", "answer_record": {"correct": False}},
        {"topic": "credit", "answer_record": {"correct": False}},
        {"topic": "credit", "answer_record": {"correct": True}},
        {"topic": "saving", "answer_record": None},
    ]
    assert _topic("S01", "2026-11-05", rows) == ("saving", "recent_wrong_answer")


def test_history_is_per_user_and_respects_requested_day(tmp_path, monkeypatch):
    monkeypatch.setenv("MOA_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("MOA_AI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)
    for day in ("2026-11-04", "2026-11-05"):
        quiz = client.get("/api/v4/users/S01/daily-quiz", params={"day": day}).json()
        answer = client.post("/api/v4/users/S01/daily-quiz/answer", params={"day": day},
                             json={"quiz_id": quiz["id"], "choice": 0})
        assert answer.status_code == 200
    history = client.get("/api/v4/users/S01/learning-history", params={"day": "2026-11-04"})
    assert history.status_code == 200
    assert history.json()["attempt_count"] == 1
    assert history.json()["items"][0]["date"] == "2026-11-04"
    assert client.get("/api/v4/users/S02/learning-history").json()["attempt_count"] == 0
    assert client.get("/api/v4/users/S99/learning-history").status_code == 404
    assert "/api/v4/users/{user_id}/learning-history" in client.get("/openapi.json").json()["paths"]


def test_interests_personalize_new_quiz_and_do_not_change_other_users(tmp_path, monkeypatch):
    monkeypatch.setenv("MOA_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("MOA_AI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)
    result = client.post("/api/v4/users/S01/learning-interests", json={"topics": ["saving"]})
    assert result.status_code == 200
    assert client.get("/api/v4/users/S01/learning-interests").json()["topics"] == ["saving"]
    assert client.get("/api/v4/users/S02/learning-interests").json()["topics"] == []
    quiz = client.get("/api/v4/users/S01/daily-quiz")
    assert quiz.status_code == 200, quiz.text
    assert quiz.json()["topic"] == "saving"
    assert quiz.json()["selection_reason"] == "user_interest"
    assert client.post("/api/v4/users/S01/learning-interests", json={"topics": ["saving", "saving"]}).status_code == 422
    assert client.post("/api/v4/users/S99/learning-interests", json={"topics": []}).status_code == 404


def test_unresolved_wrong_answer_takes_precedence_over_interests():
    rows = [{"topic": "credit", "answer_record": {"correct": False}}]
    assert _topic("S01", "2026-11-05", rows, ["saving"]) == ("credit", "recent_wrong_answer")


def test_answer_feedback_receives_selected_option(tmp_path, monkeypatch):
    monkeypatch.setenv("MOA_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("MOA_AI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)
    quiz = client.get("/api/v4/users/S01/daily-quiz").json()
    with patch("app.services.learning_ai.configured", return_value=True), \
         patch("app.services.learning_ai.structured_text", return_value=({"feedback": "선택한 답과 정답의 차이를 다시 살펴보세요."}, "test-model")) as call:
        response = client.post("/api/v4/users/S01/daily-quiz/answer", json={"quiz_id": quiz["id"], "choice": 2})
    assert response.status_code == 200
    assert response.json()["answer_feedback"]["source"] == "llm"
    assert call.call_args.args[1]["selected_option"] == quiz["options"][2]


def test_issued_quiz_stays_stable_when_ai_configuration_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("MOA_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("MOA_AI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)
    first = client.get("/api/v4/users/S01/daily-quiz").json()
    with patch("app.services.learning_ai.configured", return_value=True):
        second = client.get("/api/v4/users/S01/daily-quiz").json()
    assert second["id"] == first["id"]


def test_failed_ai_generation_is_labeled_and_keeps_quiz_usable(tmp_path, monkeypatch):
    from app.services.ai_provider import AiUnavailable
    monkeypatch.setenv("MOA_RUNTIME_DIR", str(tmp_path))
    with patch("app.services.learning_ai.configured", return_value=True), \
         patch("app.services.learning_ai.structured_text", side_effect=AiUnavailable("provider_rate_or_quota_limited")):
        result = TestClient(app).get("/api/v4/users/S03/daily-quiz").json()
    assert result["source"] == "rule_based"
    assert result["generation_status"] == "provider_rate_or_quota_limited"
    assert result["correct_answer"] is None
    assert len(result["options"]) == 4

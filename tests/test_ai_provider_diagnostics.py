"""Live AI failures must be diagnosable without exposing secrets or user data."""
import pytest
from unittest.mock import patch

from app.services.ai_provider import AiUnavailable, structured_text


class UpstreamFailure(Exception):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__("Bearer super-secret-key, prompt contents, upstream.example.com")


@pytest.mark.parametrize("status,expected", [
    (401, "provider_auth_or_permission_failed"),
    (403, "provider_auth_or_permission_failed"),
    (404, "provider_model_or_endpoint_not_found"),
    (429, "provider_rate_or_quota_limited"),
    (400, "provider_request_or_schema_incompatible"),
    (422, "provider_request_or_schema_incompatible"),
    (500, "provider_upstream_unavailable"),
])
def test_provider_rejects_upstream_errors_without_exposing_user_content(monkeypatch, status, expected):
    monkeypatch.setenv("MOA_AI_API_KEY", "super-secret-key")
    monkeypatch.setenv("MOA_AI_MODEL", "gpt-4.1-mini")
    with patch("openai.OpenAI") as cls:
        cls.return_value.chat.completions.create.side_effect = UpstreamFailure(status)
        with pytest.raises(AiUnavailable) as result:
            structured_text("private prompt", {"data": "private content"})
    assert result.value.code == expected
    assert str(result.value) == expected
    assert "super-secret-key" not in str(result.value)
    assert "private content" not in str(result.value)


def test_provider_rejects_non_json_success_without_faking_llm(monkeypatch):
    monkeypatch.setenv("MOA_AI_API_KEY", "super-secret-key")
    with patch("openai.OpenAI") as cls:
        cls.return_value.chat.completions.create.return_value.choices = [
            type("Choice", (), {"message": type("Message", (), {"content": "not json"})()})()
        ]
        with pytest.raises(AiUnavailable) as result:
            structured_text("private prompt", {"data": "private content"})
    assert result.value.code == "provider_response_invalid_json"


def test_provider_missing_key_explicitly_reported(monkeypatch):
    for key in ("MOA_AI_API_KEY", "OPENAI_API_KEY", "MOA_AGENT_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(AiUnavailable) as result:
        structured_text("brief", {"user": "S01"})
    assert result.value.code == "ai_api_key_missing"

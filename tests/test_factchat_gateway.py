"""FactChat adapter preserves strict JSON validation with standard chat-model routing."""
import json
from unittest.mock import patch
import pytest

from app.services.ai_provider import structured_text, AiUnavailable


def _response(content):
    message = type("Message", (), {"content": content})()
    choice = type("Choice", (), {"message": message})()
    return type("Response", (), {"choices": [choice]})()


def test_factchat_omits_unsupported_json_object(monkeypatch):
    monkeypatch.setenv("MOA_AI_API_KEY", "fake-key")
    monkeypatch.setenv("MOA_AI_BASE_URL", "https://factchat-cloud.mindlogic.ai/v1/gateway")
    monkeypatch.setenv("MOA_AI_MODEL", "claude-haiku-4-5-20251001")
    with patch("openai.OpenAI") as sdk:
        sdk.return_value.chat.completions.create.return_value = _response('{"explanation":"예정된 결제 일정을 확인해 보세요."}')
        result, model = structured_text("JSON only", {"source":"synthetic S01"}, max_tokens=160)
        params = sdk.return_value.chat.completions.create.call_args.kwargs
    assert result["explanation"] == "예정된 결제 일정을 확인해 보세요."
    assert model == "claude-haiku-4-5-20251001"
    assert "response_format" not in params
    assert params["temperature"] == 0
    assert sdk.call_args.kwargs["base_url"] == "https://factchat-cloud.mindlogic.ai/v1/gateway"


def test_regular_openai_still_uses_json_object_mode(monkeypatch):
    monkeypatch.setenv("MOA_AI_API_KEY", "fake-key")
    monkeypatch.setenv("MOA_AI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("MOA_AI_MODEL", "gpt-4.1-mini")
    with patch("openai.OpenAI") as sdk:
        sdk.return_value.chat.completions.create.return_value = _response('{"explanation":"결제 전 잔액을 확인하세요."}')
        structured_text("JSON only", {"source":"synthetic S01"})
        params = sdk.return_value.chat.completions.create.call_args.kwargs
    assert params["response_format"] == {"type": "json_object"}


def test_factchat_non_json_never_claims_success(monkeypatch):
    monkeypatch.setenv("MOA_AI_API_KEY", "fake-key")
    monkeypatch.setenv("MOA_AI_BASE_URL", "https://factchat-cloud.mindlogic.ai/v1/gateway")
    with patch("openai.OpenAI") as sdk:
        sdk.return_value.chat.completions.create.return_value = _response("not JSON")
        with pytest.raises(AiUnavailable) as exc:
            structured_text("JSON only", {"source":"synthetic S01"})
    assert exc.value.code == "provider_response_invalid_json"

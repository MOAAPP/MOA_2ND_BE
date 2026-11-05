"""Server-only OpenAI-compatible provider for bounded, grounded synthetic-finance text.

Never accepts a browser-supplied API key, never logs prompts, never returns a fake
provider result, and never asks the LLM to calculate monetary values.
"""
import json
import os

class AiUnavailable(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

def provider_settings(kind: str = "text") -> tuple[str, str, str]:
    prefix = "MOA_VISION" if kind == "vision" else "MOA_AGENT" if kind == "agent" else "MOA_AI"
    key = (os.getenv(prefix + "_API_KEY") or os.getenv("MOA_AI_API_KEY")
           or os.getenv("OPENAI_API_KEY") or "").strip()
    base = (os.getenv(prefix + "_BASE_URL") or os.getenv("MOA_AI_BASE_URL")
            or "https://api.openai.com/v1").strip()
    model = (os.getenv(prefix + "_MODEL") or os.getenv("MOA_AI_MODEL")
             or ("gpt-4o-mini" if kind == "vision" else "gpt-4.1-mini")).strip()
    return key, base, model

def configured(kind: str = "text") -> bool:
    return bool(provider_settings(kind)[0])

def structured_text(system: str, facts: dict, *, model: str | None = None,
                    max_tokens: int = 180) -> tuple[dict, str]:
    key, base, default_model = provider_settings()
    if not key:
        raise AiUnavailable("ai_api_key_missing")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url=base, timeout=18, max_retries=0)
        selected_model = model or default_model
        params = {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": system + "\n반드시 설명이나 코드블록 없이 JSON 객체만 응답하라."},
                {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        # FactChat's public gateway documents json_schema but not the older
        # OpenAI json_object mode. Some routed models (Claude) reject the
        # unsupported response_format with HTTP 400. We still strictly
        # validate the JSON object after the call; never fabricate an LLM
        # success for an invalid response.
        is_factchat = "factchat-cloud.mindlogic.ai/v1/gateway" in base.rstrip("/").lower()
        if is_factchat:
            # The gateway may route to a model with internal reasoning or
            # verbose JSON formatting. A 160-token output allowance can
            # truncate even a short valid JSON object mid-string.
            params["max_tokens"] = max(512, max_tokens)
        else:
            params["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**params)
        choice = response.choices[0]
        raw = choice.message.content
        if getattr(choice, "finish_reason", None) == "length":
            raise AiUnavailable("provider_output_truncated")
        if not isinstance(raw, str) or not raw.strip():
            raise AiUnavailable("provider_empty_completion")
        # Some OpenAI-compatible models return a fenced JSON object despite
        # JSON-only instructions. Strip only surrounding Markdown fences.
        text = raw.strip()
        if text.startswith("```") and text.endswith("```"):
            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline + 1:-3].strip()
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ValueError("response_not_an_object")
        return result, model or default_model
    except AiUnavailable:
        raise
    except Exception as exc:
        # Upstream SDK exceptions may include raw request text and URLs.
        # Report only the HTTP class or safe exception TYPE, never messages,
        # headers, endpoint URLs, request bodies or credentials.
        status = getattr(exc, "status_code", None)
        if status in (401, 403):
            code = "provider_auth_or_permission_failed"
        elif status == 404:
            code = "provider_model_or_endpoint_not_found"
        elif status == 429:
            code = "provider_rate_or_quota_limited"
        elif status in (400, 422):
            code = "provider_request_or_schema_incompatible"
        elif isinstance(status, int) and status >= 500:
            code = "provider_upstream_unavailable"
        elif isinstance(exc, (ValueError, TypeError, KeyError, IndexError)):
            code = "provider_response_invalid_json"
        elif type(exc).__name__ in ("APITimeoutError", "APIConnectionError",
                                    "ConnectTimeout", "ReadTimeout", "ConnectError"):
            code = "provider_network_or_timeout"
        else:
            code = "provider_call_failed"
        raise AiUnavailable(code) from exc

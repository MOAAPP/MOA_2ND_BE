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
        response = client.chat.completions.create(
            model=model or default_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=max_tokens,
        )
        raw = response.choices[0].message.content
        result = json.loads(raw or "")
        if not isinstance(result, dict):
            raise ValueError("response_not_an_object")
        return result, model or default_model
    except AiUnavailable:
        raise
    except Exception as exc:
        # Do not leak provider credentials, request bodies or internal URLs.
        raise AiUnavailable("provider_call_or_schema_failed") from exc

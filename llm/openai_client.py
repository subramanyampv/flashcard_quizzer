"""The single shared OpenAI client — reads ONLY from config (blueprint rule 8).

Every tool that talks to OpenAI imports get_client()/chat_json() from here;
no module constructs its own client or touches os.environ.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from config import get_config


class LLMUnavailableError(RuntimeError):
    """No usable LLM access: offline mode, or a failed live call."""


_client: Optional[Any] = None


def get_client() -> Any:
    """Return the cached OpenAI client; raise LLMUnavailableError in offline mode."""
    global _client
    cfg = get_config()
    if cfg.offline_mode or not cfg.settings.openai_api_key:
        raise LLMUnavailableError(
            "No OpenAI access (offline mode or missing key). Callers must use their "
            "deterministic fallback path, or set a valid OPENAI_API_KEY in .env."
        )
    if _client is None:
        from openai import OpenAI  # deferred so offline/test runs never need the SDK

        _client = OpenAI(
            api_key=cfg.settings.openai_api_key,
            base_url=cfg.settings.openai_base_url or None,
            timeout=20.0,
            max_retries=2,
        )
    return _client


def chat_json(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """One chat call that must return a JSON object.

    Returns {"data": <parsed json>, "usage": {"prompt_tokens", "completion_tokens"}}.
    Raises LLMUnavailableError on any transport/auth/model error so callers can
    apply their on_failure policy (retry → fallback).
    """
    cfg = get_config()
    try:
        client = get_client()
        resp = client.chat.completions.create(
            model=model or cfg.model,
            messages=messages,
            temperature=temperature if temperature is not None else cfg.temperature,
            max_tokens=max_tokens or cfg.max_tokens,
            response_format={"type": "json_object"},
        )
    except LLMUnavailableError:
        raise
    except Exception as exc:  # openai errors + json issues — degrade uniformly
        raise LLMUnavailableError(f"OpenAI chat call failed: {exc}") from exc

    try:
        data = json.loads(resp.choices[0].message.content)
    except (json.JSONDecodeError, IndexError) as exc:
        raise LLMUnavailableError(f"OpenAI returned non-JSON content: {exc}") from exc

    usage = getattr(resp, "usage", None)
    return {
        "data": data,
        "usage": {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
        },
    }

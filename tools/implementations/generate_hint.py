"""generate_hint tool — progressive nudges that never reveal the answer.

LLM path asks for a hint; both paths then run a leak check (policy: "Hints
must not contain the full expected answer") and degrade to an initial-letters
mask when LLM access is unavailable.
"""
from __future__ import annotations

from typing import Any, Dict

from agent import prompts
from config import get_config
from llm import openai_client


def _mask_fallback(expected: str, attempt: int) -> str:
    words = [w for w in expected.split() if w.strip()]
    if attempt <= 1:
        return f"The answer has {len(words)} key words — start with '{words[0][:2]}…'." if words else "Revisit the concept behind this card."
    return "It begins: " + " ".join(w[0].upper() + "." * max(1, len(w) - 1) for w in words)


def _leaks_answer(hint: str, expected: str) -> bool:
    h, e = hint.lower().strip(), expected.lower().strip()
    return bool(e) and (e in h or (len(e) > 20 and e[:20] in h and e[-20:] in h))


async def generate_hint(question: str = "", expected: str = "", card_id: str = "",
                        attempt: int = 1, **_ignored) -> Dict[str, Any]:
    """Produce one Hint dict for `card_id`. Returns the tool envelope."""
    if not expected.strip():
        return {"status": "error", "data": None, "error_msg": "generate_hint needs 'expected'."}
    attempt = max(1, int(attempt or 1))
    cfg = get_config()
    hint_text = None

    if not (cfg.offline_mode or not cfg.llm_available):
        messages = [
            {"role": "system", "content": prompts.SYSTEM_PROMPTS["generate_hint"]},
            {"role": "user", "content": prompts.format_prompt(
                "HINT_USER_TEMPLATE", question=question, expected=expected, attempt=attempt)},
        ]
        try:
            result = openai_client.chat_json(messages, max_tokens=min(cfg.max_tokens, 120))
            hint_text = str(result["data"].get("hint", "")).strip() or None
        except openai_client.LLMUnavailableError:
            hint_text = None  # fall through to masked hint

    if not hint_text or _leaks_answer(hint_text, expected):
        hint_text = _mask_fallback(expected, attempt)

    return {"status": "ok",
            "data": {"card_id": card_id, "attempt": attempt, "hint_text": hint_text},
            "error_msg": None}

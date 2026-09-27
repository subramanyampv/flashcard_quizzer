"""grade_answer tool — OpenAI semantic grading with deterministic fallback.

on_failure policy (PLAN §3 Quiz Agent): retry once, then degrade to a keyword
overlap scorer so quizzes complete even without LLM access. Verdicts always
conform to the agent.schemas.Verdict contract.
"""
from __future__ import annotations

import difflib
import re
from typing import Any, Dict

from agent import prompts
from agent.schemas import Verdict
from config import get_config
from llm import openai_client

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "to", "of", "in", "on", "for",
    "and", "or", "that", "this", "it", "as", "at", "by", "with", "from", "into", "its",
}


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS}


def keyword_fallback(expected: str, given: str) -> Verdict:
    """Deterministic scorer: token coverage + fuzzy similarity."""
    exp_tok, giv_tok = _tokens(expected), _tokens(given)
    coverage = len(exp_tok & giv_tok) / max(len(exp_tok), 1)
    similarity = difflib.SequenceMatcher(None, given.lower(), expected.lower()).ratio()
    score = max(coverage, similarity)
    matched = sorted(exp_tok & giv_tok)
    if score >= 0.55:
        explanation = f"Good — you covered the key terms: {', '.join(matched) or 'close wording'}."
        return Verdict(correct=True, partial=False, explanation=explanation,
                       confidence=min(0.99, 0.6 + score / 2), needs_review=False,
                       graded_by="fallback-keyword")
    if score >= 0.2:
        detail = f"you matched: {', '.join(matched)}" if matched else "some concepts overlap"
        return Verdict(correct=False, partial=True,
                       explanation=f"Partly there — {detail}. Review the full answer.",
                       confidence=0.7, needs_review=True, graded_by="fallback-keyword")
    return Verdict(correct=False, partial=False,
                   explanation="That doesn't match the expected answer — review this card.",
                   confidence=0.9, needs_review=True, graded_by="fallback-keyword")


def _validate_verdict(data: Dict[str, Any]) -> Verdict:
    return Verdict(
        correct=bool(data.get("correct", False)),
        partial=bool(data.get("partial", False)),
        explanation=str(data.get("explanation", "")).strip() or "Graded.",
        confidence=max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
        needs_review=bool(data.get("partial") or not data.get("correct"))
        and float(data.get("confidence", 1.0)) < 0.6,
        graded_by="openai",
    )


async def grade_answer(question: str = "", expected: str = "", given: str = "",
                       **_ignored) -> Dict[str, Any]:
    """Grade `given` vs `expected` for `question`. Returns the tool envelope."""
    if not (question.strip() and expected.strip()):
        return {"status": "error", "data": None,
                "error_msg": "grade_answer needs both 'question' and 'expected'."}

    cfg = get_config()
    if cfg.offline_mode or not cfg.llm_available:
        return {"status": "ok", "data": keyword_fallback(expected, given).model_dump(),
                "error_msg": None, "usage": {}}

    messages = [
        {"role": "system", "content": prompts.SYSTEM_PROMPTS["grade_answer"]},
        {"role": "user", "content": prompts.format_prompt(
            "GRADE_USER_TEMPLATE", question=question, expected=expected, given=given)},
    ]
    last_error = None
    for _attempt in range(2):  # on_failure: retry once, then fall back
        try:
            result = openai_client.chat_json(messages, max_tokens=min(cfg.max_tokens, 300))
            verdict = _validate_verdict(result["data"])
            return {"status": "ok", "data": verdict.model_dump(),
                    "error_msg": None, "usage": result["usage"]}
        except openai_client.LLMUnavailableError as exc:
            last_error = exc
    return {"status": "ok", "data": keyword_fallback(expected, given).model_dump(),
            "error_msg": f"LLM grading unavailable, used keyword fallback ({last_error})",
            "usage": {}}

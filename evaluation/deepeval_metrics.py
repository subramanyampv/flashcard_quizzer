"""Metric definitions for the QA agent's evaluation gate.

Uses deepeval's GEval when available AND OpenAI access is live; otherwise a
deterministic correctness comparator built on the same Verdict contract the
quiz uses. Both report a 0..1 score per rubric case.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def correctness_match(verdict: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    """Agreement between a grading verdict and the rubric's expectation."""
    if bool(verdict.get("correct")) != bool(expected.get("correct")):
        return False
    # partial only matters when the answer is not fully correct
    if not expected.get("correct") and bool(verdict.get("partial", False)) != bool(
            expected.get("partial", False)):
        return False
    return True


def deepeval_available() -> bool:
    try:
        import deepeval  # noqa: F401
        return True
    except ImportError:
        return False


def geval_metric(model: Optional[str] = None):
    """Optional LLM-judge second opinion; None when deepeval/keys are absent."""
    if not deepeval_available():
        return None
    try:
        from deepeval.test_case import LLMTestCaseParams
        from deepeval.metrics import GEval

        return GEval(
            name="GradingCorrectness",
            criteria="Does the system's verdict (correct/partial/incorrect) match the "
                     "expected verdict for the learner's answer?",
            evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT,
                               LLMTestCaseParams.EXPECTED_OUTPUT],
            model=model,
        )
    except Exception:
        return None

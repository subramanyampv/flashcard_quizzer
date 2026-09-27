"""Pydantic data contracts shared across every module boundary.

No bare dicts cross agent/tool/UI boundaries (PLAN.md Pillar 2): all
inter-module payloads are instances of the models defined here.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class QuizMode(str, Enum):
    """The three quiz modes from the design doc (§3 Quiz Agent)."""

    SEQUENTIAL = "sequential"
    RANDOM = "random"
    ADAPTIVE = "adaptive"


class Flashcard(BaseModel):
    """One validated question/answer pair."""

    id: str
    question: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)
    tags: List[str] = Field(default_factory=list)
    difficulty: Optional[str] = "medium"


class CardError(BaseModel):
    """A friendly, structured reason why a card could not be loaded."""

    line_no: Optional[int] = None
    raw_text: Optional[str] = None
    reason: str


class Verdict(BaseModel):
    """Result of grading one learner answer."""

    correct: bool
    partial: bool = False
    explanation: str
    confidence: float = Field(ge=0.0, le=1.0)
    needs_review: bool = False
    graded_by: str = "openai"  # "openai" | "fallback-keyword"


class Hint(BaseModel):
    """A progressive nudge that must not reveal the answer outright."""

    card_id: str
    attempt: int
    hint_text: str


class Score(BaseModel):
    """Live score state handed to the UI on every turn."""

    total_cards: int
    answered: int
    correct: int
    accuracy: float
    streak: int
    missed_cards: List[Flashcard] = Field(default_factory=list)


class TraceEvent(BaseModel):
    """One observability record: latency, tokens and USD cost per call."""

    trace_id: str
    agent: str
    tool: Optional[str] = None
    input_summary: str
    output_summary: str
    latency_ms: float
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[float] = None
    error: Optional[str] = None

"""Quiz Agent — core logic & scoring (design doc §3, PLAN §3).

Takes a CLEAN list[Flashcard] from the Data Agent (never raw files) and runs
one of three modes:

  sequential — deck order as loaded, each card once
  random     — shuffled once up front, each card once
  adaptive   — shuffled; a missed/partial/low-confidence card is RE-QUEUED
               at most twice, then recorded as a final miss (no infinite loop)

Grading goes through the tool registry (grade_answer), which itself retries
OpenAI and falls back to the keyword scorer — so a quiz always finishes.
"""
from __future__ import annotations

import random
from collections import deque
from typing import Any, Dict, List, Optional

from agent.schemas import Flashcard, QuizMode, Score, Verdict
from tools.implementations.grade_answer import keyword_fallback
from tools.tool_registry import call_tool

MAX_REQUEUES = 2  # plan metric: "re-queues missed cards exactly up to 2 times"


class QuizEngine:
    """Stateful quiz session for one deck + mode."""

    name = "quiz_agent"

    def __init__(self, cards: List[Flashcard], mode: str | QuizMode,
                 trace_id: str = "session", seed: Optional[int] = None):
        self.mode = QuizMode(mode)
        self.trace_id = trace_id
        self._all: List[Flashcard] = list(cards)
        order = list(cards)
        if self.mode in (QuizMode.RANDOM, QuizMode.ADAPTIVE):
            random.Random(seed).shuffle(order)
        self._queue: deque = deque(order)
        self._requeues: Dict[str, int] = {}
        self._hint_attempts: Dict[str, int] = {}
        self._final_missed: Dict[str, Flashcard] = {}
        self.answered = 0
        self.correct = 0
        self.partial = 0
        self.streak = 0
        self.best_streak = 0

    # ── read-only view for the UI ─────────────────────────────────────
    @property
    def next_card(self) -> Optional[Flashcard]:
        return self._queue[0] if self._queue else None

    @property
    def finished(self) -> bool:
        return not self._queue

    def score(self) -> Score:
        return Score(
            total_cards=len(self._all),
            answered=self.answered,
            correct=self.correct,
            accuracy=round(self.correct / self.answered, 4) if self.answered else 0.0,
            streak=self.streak,
            missed_cards=list(self._final_missed.values()),
        )

    # ── the one turn method the Manager calls ─────────────────────────
    async def submit_answer(self, given: str) -> Dict[str, Any]:
        """Grade the answer to the current card and advance the queue."""
        card = self.next_card
        if card is None:
            return {"verdict": None, "score": self.score(), "next_card": None,
                    "finished": True, "message": "This quiz is already finished."}

        given = (given or "").strip()
        if not given:
            return {"verdict": None, "score": self.score(), "next_card": card,
                    "finished": False, "message": "Type an answer (or 'skip' to move on)."}

        self._queue.popleft()  # the card is consumed either way; re-queue adds it back
        if given.lower() in ("skip", "pass", "?"):
            verdict = Verdict(correct=False, partial=False, explanation="Skipped — no answer given.",
                              confidence=1.0, needs_review=True, graded_by="none")
        else:
            verdict = await self._grade(card, given)

        self.answered += 1
        if verdict.correct:
            self.correct += 1
            self.streak += 1
            self.best_streak = max(self.best_streak, self.streak)
        else:
            if verdict.partial:
                self.partial += 1
            self.streak = 0
            self._handle_miss(card)

        stats = None
        if self.finished:
            stats = await self._persist()
        return {"verdict": verdict, "score": self.score(), "next_card": self.next_card,
                "finished": self.finished, "message": verdict.explanation, "stats": stats}

    async def _grade(self, card: Flashcard, given: str) -> Verdict:
        """Tool registry path; double safety net if the tool itself errored."""
        result = await call_tool("grade_answer",
                                 {"question": card.question, "expected": card.answer, "given": given},
                                 caller="quiz_agent")
        if result["status"] == "ok" and isinstance(result["data"], dict):
            return Verdict(**result["data"])
        return keyword_fallback(card.answer, given)

    def _handle_miss(self, card: Flashcard) -> None:
        """Adaptive re-queue (max MAX_REQUEUES), else record final miss."""
        if self.mode == QuizMode.ADAPTIVE and self._requeues.get(card.id, 0) < MAX_REQUEUES:
            self._requeues[card.id] = self._requeues.get(card.id, 0) + 1
            self._queue.append(card)
        else:
            self._final_missed[card.id] = card

    # ── hints ─────────────────────────────────────────────────────────
    async def hint(self, card: Optional[Flashcard] = None) -> Dict[str, Any]:
        card = card or self.next_card
        if card is None:
            return {"status": "error", "data": None, "error_msg": "No card in play."}
        attempt = self._hint_attempts.get(card.id, 0) + 1
        self._hint_attempts[card.id] = attempt
        return await call_tool("generate_hint",
                               {"question": card.question, "expected": card.answer,
                                "card_id": card.id, "attempt": attempt},
                               caller="quiz_agent")

    # ── results ───────────────────────────────────────────────────────
    async def _persist(self) -> Optional[Dict[str, Any]]:
        result = await call_tool("track_stats",
                                 {"trace_id": self.trace_id, "mode": self.mode.value,
                                  "stats": {**self.score().model_dump(exclude={"missed_cards"}),
                                            "partial": self.partial, "best_streak": self.best_streak,
                                            "missed_ids": list(self._final_missed)}},
                                 caller="quiz_agent")
        return result.get("data")

    def results_summary(self) -> str:
        s = self.score()
        lines = [f"Quiz complete ({self.mode.value}) — {s.correct}/{s.total_cards} correct"
                 f" ({round(s.accuracy * 100)}%)."]
        if self.mode == QuizMode.ADAPTIVE and s.missed_cards:
            lines.append("Final misses: " + "; ".join(c.question for c in s.missed_cards))
        if self.best_streak > 1:
            lines.append(f"Best streak: {self.best_streak}.")
        return " ".join(lines)

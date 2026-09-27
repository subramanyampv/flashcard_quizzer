"""Session (short-term, in-process) + long-term (feedback store) memory."""
from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, List, Optional

from policy_rlhf.feedback_collector import _load as _load_feedback


class SessionMemory:
    """Last N turns of the current quiz conversation. Default N=10 (blueprint)."""

    def __init__(self, max_turns: int = 10):
        self._turns: Deque[Dict[str, str]] = deque(maxlen=max_turns)

    def add(self, role: str, content: str) -> None:
        self._turns.append({"role": role, "content": content})

    def get_history(self) -> List[Dict[str, str]]:
        return list(self._turns)

    def clear(self) -> None:
        self._turns.clear()


class LongTermMemory:
    """Reads RLHF-style feedback collected across sessions (read-only view)."""

    def ratings_for(self, agent: Optional[str] = None) -> List[Dict[str, Any]]:
        return [r for r in _load_feedback() if agent is None or r.get("agent") == agent]

    def summary(self, last_n: int = 50) -> Dict[str, Any]:
        recent = _load_feedback()[-last_n:]
        if not recent:
            return {"n": 0, "avg_rating": None, "top_comments": []}
        ratings = [r.get("rating", 0) for r in recent]
        comments = [r["comment"] for r in recent if r.get("comment")]
        return {"n": len(recent), "avg_rating": round(sum(ratings) / len(ratings), 3),
                "top_comments": comments[:5]}

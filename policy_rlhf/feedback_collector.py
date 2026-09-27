"""Feedback collector — thumbs up/down + comment → data/feedback store.

Feeds LongTermMemory (agent/memory.py) so later sessions can learn from
explicit learner ratings.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import PROJECT_ROOT

FEEDBACK_FILE = PROJECT_ROOT / "data" / "feedback" / "feedback_store.json"


def _load() -> List[Dict[str, Any]]:
    try:
        data = json.loads(FEEDBACK_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def collect(trace_id: str, rating: int, comment: str = "",
            agent: str = "manager_agent") -> Optional[Dict[str, Any]]:
    """Append one feedback record. Returns the stored entry (None on failure)."""
    rating = max(-1, min(1, int(rating)))  # -1 / 0 / +1
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "trace_id": trace_id,
             "agent": agent, "rating": rating, "comment": comment[:500]}
    records = _load()
    records.append(entry)
    try:
        FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        FEEDBACK_FILE.write_text(json.dumps(records, indent=2), encoding="utf-8")
        return entry
    except OSError:
        return None


def recent(n: int = 20) -> List[Dict[str, Any]]:
    return _load()[-n:]

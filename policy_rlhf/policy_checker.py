"""Policy checker — validates agent actions against data/policy/policy.json.

Rules in the JSON are human-readable; each carries an id so code can enforce
it. Unenforced rules still surface as `warnings` so the UI can display them.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Dict, List

from config import PROJECT_ROOT

POLICY_FILE = PROJECT_ROOT / "data" / "policy" / "policy.json"


@lru_cache(maxsize=1)
def load_policy() -> List[Dict[str, Any]]:
    try:
        return json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def reload_policy() -> None:
    load_policy.cache_clear()


def check(agent_name: str, action: Dict[str, Any]) -> Dict[str, Any]:
    """Return {"allowed": bool, "reason": str|None, "warnings": [rule,...]}.

    Machine-enforced guards (action dict keys are set by callers):
      complete_cards=True   — every card in payload has question AND answer
      requeue_limit=(n,max) — n requeues must not exceed max (policy.json rule 2)
      pii_clean=True        — payload text already passed guardrail redaction
    """
    rules = load_policy()
    applicable = [r for r in rules if agent_name in r.get("applies_to", [])]
    warnings = [r["rule"] for r in applicable if r.get("action") == "warn"]

    guards = action.get("guards", {})
    if "complete_cards" in guards and not guards["complete_cards"]:
        rule = _rule_for(applicable, "missing question or answer")
        return {"allowed": False, "warnings": warnings,
                "reason": rule or "Cards with missing question/answer may not proceed."}
    if "requeue_limit" in guards:
        used, cap = guards["requeue_limit"]
        if used > cap:
            return {"allowed": False, "warnings": warnings,
                    "reason": f"Adaptive re-queue cap ({cap}) exceeded for a card."}
    if "pii_clean" in guards and not guards["pii_clean"]:
        return {"allowed": False, "warnings": warnings,
                "reason": "PII detected in text being logged."}
    return {"allowed": True, "reason": None, "warnings": warnings}


def _rule_for(rules: List[Dict[str, Any]], needle: str):
    for r in rules:
        if needle in r["rule"]:
            return r["rule"]
    return None

"""Central tool registry: name → callable + metadata (blueprint §6).

Agents call tools ONLY through call_tool() — same shape as the deferred MCP
client (mcp/client.py in the blueprint), so a protocol wrapper can be added
later without touching any agent code.
"""
from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Dict, List, Optional

from tools.implementations import (
    generate_hint,
    grade_answer,
    load_flashcard_file,
    log_trace,
    track_stats,
)

TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "load_flashcard_file": {
        "fn": load_flashcard_file.load_flashcard_file,
        "description": "Parse a flashcard file (json/csv/txt/md) into validated card dicts",
        "schema": {"source": {"type": "string|bytes", "required": True},
                   "format": {"type": "string", "required": False}},
    },
    "grade_answer": {
        "fn": grade_answer.grade_answer,
        "description": "Semantically compare a learner answer vs expected via OpenAI",
        "schema": {"question": {"type": "string", "required": True},
                   "expected": {"type": "string", "required": True},
                   "given": {"type": "string", "required": True}},
    },
    "generate_hint": {
        "fn": generate_hint.generate_hint,
        "description": "Produce a progressive nudge without revealing the answer",
        "schema": {"question": {"type": "string", "required": False},
                   "expected": {"type": "string", "required": True},
                   "card_id": {"type": "string", "required": False},
                   "attempt": {"type": "int", "required": False}},
    },
    "track_stats": {
        "fn": track_stats.track_stats,
        "description": "Persist session score + missed cards to data/stats/ as JSON",
        "schema": {"trace_id": {"type": "string", "required": True},
                   "mode": {"type": "string", "required": False},
                   "stats": {"type": "object", "required": True}},
    },
    "log_trace": {
        "fn": log_trace.log_trace,
        "description": "Record a TraceEvent (Langfuse or local JSON-lines)",
        "schema": {"trace_id": {"type": "string", "required": True},
                   "agent": {"type": "string", "required": True},
                   "input_summary": {"type": "string", "required": False},
                   "output_summary": {"type": "string", "required": False},
                   "latency_ms": {"type": "float", "required": False}},
    },
}

# Static Section 2↔3 wiring: which agent may call which tool
AGENT_TOOLS: Dict[str, List[str]] = {
    "manager_agent": ["track_stats", "log_trace"],
    "data_agent": ["load_flashcard_file"],
    "quiz_agent": ["grade_answer", "generate_hint", "track_stats"],
    "ui_agent": [],
    "qa_agent": ["run_eval_suite"],
}


def get_tool(name: str) -> Callable[..., Awaitable[Dict[str, Any]]]:
    if name not in TOOL_REGISTRY:
        raise KeyError(f"Unknown tool '{name}'. Registered: {sorted(TOOL_REGISTRY)}")
    return TOOL_REGISTRY[name]["fn"]


def list_tools_for_agent(agent_name: str) -> List[str]:
    return list(AGENT_TOOLS.get(agent_name, []))


async def call_tool(name: str, params: Optional[Dict[str, Any]] = None,
                    caller: Optional[str] = None) -> Dict[str, Any]:
    """Validate params against the tool schema, run it, normalize failures.

    Always returns the {"status","data","error_msg"} envelope — agents never
    have to catch exceptions from tools.
    """
    params = dict(params or {})
    if name not in TOOL_REGISTRY:
        return {"status": "error", "data": None, "error_msg": f"Unknown tool '{name}'."}
    if caller and name not in list_tools_for_agent(caller):
        return {"status": "error", "data": None,
                "error_msg": f"Agent '{caller}' is not allowed to call '{name}'."}

    entry = TOOL_REGISTRY[name]
    sig = inspect.signature(entry["fn"])
    allowed = set(sig.parameters) - {"_ignored"}
    for pname, meta in entry["schema"].items():
        if meta.get("required") and pname not in params:
            return {"status": "error", "data": None, "error_msg": f"Missing required param '{pname}'."}
    filtered = {k: v for k, v in params.items() if k in allowed and k != "_ignored"}

    try:
        result = await entry["fn"](**filtered)
        if isinstance(result, dict) and "status" in result:
            return result
        return {"status": "ok", "data": result, "error_msg": None}
    except Exception as exc:  # tools must never crash the pipeline
        return {"status": "error", "data": None, "error_msg": f"Tool '{name}' failed: {exc}"}

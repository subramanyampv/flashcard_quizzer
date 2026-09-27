"""Dual observability engine (PLAN.md Pillar 5).

Native Langfuse when keys are configured (spans for agent steps, generations
for LLM calls); otherwise appends JSON lines to logs/interactions.log and
computes USD cost locally from the built-in pricing table.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from agent.schemas import TraceEvent
from config import LOGS_DIR, PROJECT_ROOT, get_config

# USD per 1M tokens (PLAN §4.2)
PRICING: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.150 / 1_000_000, "output": 0.600 / 1_000_000},
    "text-embedding-3-small": {"input": 0.020 / 1_000_000, "output": 0.0},
}

INTERACTIONS_LOG = LOGS_DIR / "interactions.log"
ERRORS_LOG = LOGS_DIR / "errors.log"

_langfuse = None
_langfuse_failed = False


def compute_cost_usd(model: str, tokens_in: Optional[int], tokens_out: Optional[int]) -> Optional[float]:
    rates = PRICING.get(model)
    if not rates or (tokens_in is None and tokens_out is None):
        return None
    cost = (tokens_in or 0) * rates["input"] + (tokens_out or 0) * rates["output"]
    return round(cost, 8)


def log_trace(trace_id: str, agent: str, input_summary: str, output_summary: str,
              latency_ms: float, tool: Optional[str] = None, tokens_in: Optional[int] = None,
              tokens_out: Optional[int] = None, error: Optional[str] = None,
              model: Optional[str] = None) -> TraceEvent:
    """Emit one TraceEvent to Langfuse or the local JSON-lines fallback."""
    cfg = get_config()
    event = TraceEvent(
        trace_id=trace_id, agent=agent, tool=tool,
        input_summary=input_summary[:300], output_summary=output_summary[:300],
        latency_ms=round(latency_ms, 2), tokens_in=tokens_in, tokens_out=tokens_out,
        cost_usd=compute_cost_usd(model or cfg.model, tokens_in, tokens_out), error=error,
    )
    if error:
        _append_json_line(ERRORS_LOG, event.model_dump())
    _dispatch(event)
    return event


def _dispatch(event: TraceEvent) -> None:
    global _langfuse, _langfuse_failed
    settings = _safe_settings()
    if settings and settings.langfuse_public_key and settings.langfuse_secret_key and not _langfuse_failed:
        try:
            if _langfuse is None:
                from langfuse import Langfuse  # guarded: optional dependency

                _langfuse = Langfuse(public_key=settings.langfuse_public_key,
                                     secret_key=settings.langfuse_secret_key,
                                     host=settings.langfuse_host)
            if event.tool:
                gen = _langfuse.generation(
                    name=f"{event.agent}:{event.tool}", trace_id=event.trace_id,
                    input=event.input_summary, output=event.output_summary,
                    model=settings.openai_model,
                    usage={"input": event.tokens_in or 0, "output": event.tokens_out or 0},
                    level="ERROR" if event.error else "DEFAULT",
                )
                gen.end()
            else:
                span = _langfuse.span(name=f"{event.agent}", trace_id=event.trace_id,
                                      input=event.input_summary, output=event.output_summary)
                span.end()
            return
        except Exception:  # never let observability take the app down
            _langfuse_failed = True
    _append_json_line(INTERACTIONS_LOG, event.model_dump())


def _safe_settings():
    try:
        return get_config().settings
    except Exception:
        return None


def _append_json_line(path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": datetime.now(timezone.utc).isoformat(), **payload}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError:  # pragma: no cover
        pass

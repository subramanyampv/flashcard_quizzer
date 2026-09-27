"""log_trace tool — thin callable wrapper over the observability engine."""
from __future__ import annotations

from typing import Any, Dict, Optional

from monitoring.langfuse_logger import log_trace as _log_trace


async def log_trace(trace_id: str = "", agent: str = "", input_summary: str = "",
                    output_summary: str = "", latency_ms: float = 0.0,
                    tool: Optional[str] = None, error: Optional[str] = None,
                    **_ignored) -> Dict[str, Any]:
    """Record one trace event. Returns the tool envelope with the TraceEvent."""
    if not trace_id or not agent:
        return {"status": "error", "data": None, "error_msg": "log_trace needs 'trace_id' and 'agent'."}
    event = _log_trace(trace_id=trace_id, agent=agent, tool=tool,
                       input_summary=input_summary, output_summary=output_summary,
                       latency_ms=latency_ms, error=error)
    return {"status": "ok", "data": event.model_dump(), "error_msg": None}

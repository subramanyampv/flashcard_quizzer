"""track_stats tool — persists session metrics as JSON files (no DB).

One file per session under data/stats/{trace_id}.json, plus a running
summary in data/stats/history.json so the UI can show trends.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

from config import PROJECT_ROOT

STATS_DIR = PROJECT_ROOT / "data" / "stats"


async def track_stats(trace_id: str = "", mode: str = "", stats: Dict[str, Any] | None = None,
                      **_ignored) -> Dict[str, Any]:
    """Write a session stats payload. Returns the tool envelope."""
    if not trace_id or stats is None:
        return {"status": "error", "data": None, "error_msg": "track_stats needs 'trace_id' and 'stats'."}
    safe_id = "".join(c for c in trace_id if c.isalnum() or c in "-_")[:64] or "session"
    payload = {"trace_id": safe_id, "mode": mode, "saved_at": datetime.now(timezone.utc).isoformat(),
               "stats": stats}
    try:
        STATS_DIR.mkdir(parents=True, exist_ok=True)
        path = STATS_DIR / f"{safe_id}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        history_file = STATS_DIR / "history.json"
        history = json.loads(history_file.read_text()) if history_file.exists() else []
        history.append({"trace_id": safe_id, "mode": mode, "saved_at": payload["saved_at"],
                        "accuracy": stats.get("accuracy"), "answered": stats.get("answered")})
        history_file.write_text(json.dumps(history[-200:], indent=2), encoding="utf-8")
        return {"status": "ok", "data": {"saved_path": str(path)}, "error_msg": None}
    except OSError as exc:
        return {"status": "error", "data": None, "error_msg": f"Could not write stats: {exc}"}

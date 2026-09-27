"""Thin wrapper around st.session_state (blueprint §6: ui/state.py).

Keeps all Streamlit state mutation in one place so components.py and app.py
stay free of session-state bookkeeping. No business logic here.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def init_state(orchestrator: Any, startup_report: Any) -> None:
    import streamlit as st

    if "messages" not in st.session_state:
        st.session_state.messages = []
    st.session_state.orchestrator = orchestrator
    st.session_state.startup_report = startup_report
    st.session_state.setdefault("last_trace_id", None)
    st.session_state.setdefault("last_escalated", False)
    st.session_state.setdefault("last_confidence", 1.0)
    st.session_state.setdefault("revealed", False)


def append_turn(role: str, content: str) -> None:
    import streamlit as st

    st.session_state.messages.append({"role": role, "content": content})


def record_result(result: Dict[str, Any]) -> None:
    """Store trace_id / confidence / escalation from an orchestrator reply."""
    import streamlit as st

    st.session_state.last_trace_id = result.get("trace_id")
    st.session_state.last_confidence = result.get("confidence", 1.0)
    st.session_state.last_escalated = result.get("escalated", False)


def get_history() -> List[Dict[str, str]]:
    import streamlit as st

    return list(st.session_state.get("messages", []))


def reset_reveal() -> None:
    import streamlit as st

    st.session_state.revealed = False


def get(key: str, default: Optional[Any] = None) -> Optional[Any]:
    import streamlit as st

    return st.session_state.get(key, default)

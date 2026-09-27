"""Reusable render helpers — pure functions over st.* (blueprint §6).

No business logic: every component takes data and renders it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def startup_error(report_text: str) -> None:
    import streamlit as st

    st.error("### Startup gate failed — fix these before using the quizzer\n\n```\n"
             + report_text + "\n```")


def sidebar_meta(trace_id: Optional[str], confidence: float, escalated: bool,
                 offline_mode: bool) -> None:
    import streamlit as st

    st.sidebar.divider()
    st.sidebar.caption(f"trace: `{trace_id or '—'}`")
    st.sidebar.caption(f"confidence: {confidence:.2f}")
    if escalated:
        st.sidebar.warning("⚑ This answer was flagged for review (escalated).")
    if offline_mode:
        st.sidebar.info("Offline mode: answers are graded by the deterministic "
                        "keyword fallback (no OpenAI key / validation off).")


def score_bar(score: Dict[str, Any], total_in_deck: int) -> None:
    import streamlit as st

    answered = score.get("answered", 0)
    progress = min(1.0, answered / max(total_in_deck, 1))
    st.progress(progress, text=(f"answered {answered} · correct {score.get('correct', 0)} · "
                                f"accuracy {round(score.get('accuracy', 0) * 100)}% · "
                                f"streak {score.get('streak', 0)}"))


def verdict_badge(verdict: Optional[Dict[str, Any]]) -> None:
    import streamlit as st

    if not verdict:
        return
    if verdict.get("correct"):
        st.success(f"✓ Correct — {verdict.get('explanation', '')}")
    elif verdict.get("partial"):
        st.warning(f"◐ Partial — {verdict.get('explanation', '')}")
    else:
        st.error(f"✗ Not quite — {verdict.get('explanation', '')}")
    if verdict.get("graded_by") == "fallback-keyword":
        st.caption("graded offline by keyword fallback")


def error_banner(errors: List[Dict[str, Any]]) -> None:
    import streamlit as st

    for err in errors[:10]:
        where = f" (line {err['line_no']})" if err.get("line_no") else ""
        st.sidebar.error(f"⚠ {err['reason']}{where}")


def result_screen(score: Dict[str, Any], missed: List[str], mode: str) -> None:
    import streamlit as st

    st.subheader(f"Results — {mode} mode")
    cols = st.columns(4)
    cols[0].metric("Answered", score.get("answered", 0))
    cols[1].metric("Correct", score.get("correct", 0))
    cols[2].metric("Accuracy", f"{round(score.get('accuracy', 0) * 100)}%")
    cols[3].metric("Missed", len(missed))
    if missed:
        with st.expander("Missed cards to review"):
            for q in missed:
                st.write(f"• {q}")


def feedback_widget(trace_id: Optional[str]) -> None:
    """Thumbs up/down + comment → policy_rlhf.feedback_collector."""
    import streamlit as st

    from policy_rlhf.feedback_collector import collect

    if not trace_id:
        return
    st.sidebar.divider()
    st.sidebar.caption("Was the last answer helpful?")
    comment = st.sidebar.text_input("Optional comment", key=f"fb_comment_{trace_id}")
    c1, c2 = st.sidebar.columns(2)
    if c1.button("👍", key=f"up_{trace_id}"):
        collect(trace_id, rating=1, comment=comment or "")
        st.sidebar.success("Thanks — feedback saved.")
    if c2.button("👎", key=f"down_{trace_id}"):
        collect(trace_id, rating=-1, comment=comment or "")
        st.sidebar.success("Noted — that goes into the improvement loop.")
        st.sidebar.success("Sorry about that — noted for improvement.")

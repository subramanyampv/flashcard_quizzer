"""UI Agent — Streamlit entrypoint (blueprint §6: ui/app.py).

Run:  streamlit run ui/app.py
Startup: config + live LLM gate (blocks with troubleshooting text on failure),
then a chat-style quiz surface. All logic lives in the agents; this file only
wires input → orchestrator.process() → render helpers.
"""
from __future__ import annotations

import asyncio

import streamlit as st

from agent.core_agent import MultiAgentOrchestrator
from config import PROJECT_ROOT, Config, ConfigError
from ui import components, state

st.set_page_config(page_title="Flashcard Quizzer", page_icon="🃏", layout="wide")


# ── Tier-3 startup gate (blocks the UI with diagnostics on failure) ────
try:
    _cfg = Config.load()
    _report = _cfg.run_startup_gate()
except ConfigError as exc:
    components.startup_error(str(exc))
    st.stop()


def _get_orchestrator() -> MultiAgentOrchestrator:
    if "orchestrator" not in st.session_state:
        state.init_state(MultiAgentOrchestrator(_cfg), _report)
    return st.session_state.orchestrator


def _run(coro):
    return asyncio.run(coro)


def _handle(text: str, orch: MultiAgentOrchestrator) -> None:
    """Map a chat line (or slash command) to one orchestrator action."""
    state.append_turn("user", text)
    low = text.strip().lower()
    if low.startswith("/"):
        cmd, _, arg = low[1:].partition(" ")
        if cmd == "hint":
            r = _run(orch.process("hint", {}))
        elif cmd == "skip":
            r = _run(orch.process("answer", {"answer": "skip"}))
        elif cmd == "start":
            r = _run(orch.process("start_quiz", {"mode": arg or "sequential"}))
        elif cmd == "reset":
            orch.__init__(_cfg)  # fresh session: drops deck + engine + memory
            state.init_state(orch, _report)
            st.session_state.messages = []
            st.rerun()
        else:
            r = {"response": f"Unknown command /{cmd}. Try /hint, /skip, /start <mode>, /reset.",
                 "confidence": 0.0, "escalated": False, "data": {}, "trace_id": None,
                 "next_step": None}
    else:
        if orch.engine is None and not orch.deck:
            r = {"response": "Pick a deck in the sidebar first (sample or upload).",
                 "confidence": 0.0, "escalated": False, "data": {}, "trace_id": None,
                 "next_step": None}
        elif orch.engine is None:  # deck loaded, first line starts the quiz
            mode = st.session_state.get("mode_sel", "sequential")
            r = _run(orch.process("start_quiz", {"mode": mode}))
        else:
            r = _run(orch.process("answer", {"answer": text}))
    state.record_result(r)
    st.session_state.last_verdict = r["data"].get("verdict")
    state.append_turn("manager", r["response"])
    if r["data"].get("finished"):
        st.session_state["quiz_done"] = {
            "score": r["data"].get("score", {}),
            "mode": orch.engine.mode.value if orch.engine else "?",
            "missed": [c["question"] for c in (r["data"].get("score", {}).get("missed_cards") or [])],
        }
        st.balloons()


def _sidebar(orch: MultiAgentOrchestrator) -> None:
    st.sidebar.title("🃏 Flashcard Quizzer")
    mode = st.sidebar.selectbox("Quiz mode", ["sequential", "random", "adaptive"],
                                key="mode_sel")
    st.sidebar.caption("Sequential keeps file order · Random shuffles · "
                       "Adaptive re-queues misses up to 2×.")

    if st.sidebar.button("▶ Start / restart quiz with selected mode"):
        r = _run(orch.process("start_quiz", {"mode": mode}))
        state.record_result(r)
        state.append_turn("manager", r["response"])

    with st.sidebar.expander("📚 Sample decks", expanded=False):
        samples_dir = PROJECT_ROOT / "data" / "sample_decks"
        for f in sorted(samples_dir.glob("*")) if samples_dir.exists() else []:
            if st.button(f.name, key=f"sample_{f.name}"):
                r = _run(orch.process("load_deck", {"file_source": str(f)}))
                state.record_result(r)
                state.append_turn("manager", r["response"])
                if r["data"].get("errors"):
                    components.error_banner(r["data"]["errors"])

    uploaded = st.sidebar.file_uploader("…or upload your own", type=["json", "csv", "txt", "md"],
                                        key="deck_upload")
    if uploaded is not None:
        upload_id = (uploaded.name, uploaded.size)
        if state.get("last_upload") != upload_id:  # load_deck re-fires on every rerun otherwise
            st.session_state.last_upload = upload_id
            r = _run(orch.process("load_deck",
                                  {"file_source": uploaded.getvalue(),
                                   "file_format": uploaded.type or ""}))
            state.record_result(r)
            state.append_turn("manager", r["response"])
            if r["data"].get("errors"):
                components.error_banner(r["data"]["errors"])

    score = None
    if orch.engine is not None:
        score = orch.engine.score().model_dump()
        components.score_bar(score, len(orch.deck))
    components.sidebar_meta(state.get("last_trace_id"),
                            state.get("last_confidence", 1.0),
                            bool(state.get("last_escalated")),
                            _cfg.offline_mode)
    components.feedback_widget(state.get("last_trace_id"))
    with st.sidebar.expander("🩺 Startup health", expanded=False):
        st.code(_report.render(), language=None)


def main() -> None:
    orch = _get_orchestrator()
    _sidebar(orch)

    st.title("Flashcard Quizzer — multi-agent study sessions")
    st.caption("Manager → Data → Quiz pipeline with AI grading. "
               "Commands: `/hint` `/skip` `/start random` `/reset`.")

    for msg in state.get_history():
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    components.verdict_badge(state.get("last_verdict"))

    if orch.engine is not None and orch.engine.next_card is not None and not state.get("revealed"):
        if st.button("🔄 Flip card / show answer"):
            st.session_state.revealed = True
    card = orch.engine.next_card if orch.engine else None
    if card is not None and st.session_state.get("revealed"):
        st.info(f"**Answer:** {card.answer}")

    done = st.session_state.pop("quiz_done", None)
    if done:
        components.result_screen(done["score"], done["missed"], done["mode"])

    prompt = st.chat_input(
        "Type your answer…" if orch.engine else
        ("Answer the card above / /hint" if orch.deck else "Load a deck from the sidebar to begin…"))
    if prompt:
        _handle(prompt, orch)
        st.rerun()


main()

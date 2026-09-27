"""Manager Agent — MultiAgentOrchestrator (design doc §3, blueprint §6).

The ONLY module the UI talks to. Routes actions to specialists, enforces the
boundary rules that make the multi-agent design trustworthy:

  • uploaded files go ONLY to the Data Agent, never to the Quiz Agent;
  • a deck with zero valid cards can never start a quiz (design doc §5);
  • guardrails screen every input; policy checks gate every handoff;
  • every turn emits one TraceEvent with latency + redacted summaries.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional, Union

from agent.agents.data_agent import DataAgent
from agent.agents.quiz_agent import QuizEngine
from agent.memory import LongTermMemory, SessionMemory
from agent.schemas import CardError, Flashcard, QuizMode
from config import Config, get_config
from monitoring.langfuse_logger import log_trace
from policy_rlhf import policy_checker
from safety import guardrails

VALID_MODES = {m.value for m in QuizMode}


class MultiAgentOrchestrator:
    """Hierarchical manager coordinating data_agent → quiz_agent turns."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()
        self.data_agent = DataAgent()
        self.memory = SessionMemory()
        self.long_term = LongTermMemory()
        self.deck: List[Flashcard] = []
        self.engine: Optional[QuizEngine] = None

    # ── the single entrypoint (blueprint contract) ────────────────────
    async def process(self, action: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Route one user action. NEVER raises: failures become friendly replies."""
        context = dict(context or {})
        trace_id = context.get("trace_id") or uuid.uuid4().hex[:16]
        t0 = time.perf_counter()
        try:
            result = await self._route(action, context, trace_id)
        except Exception as exc:  # on_failure: default_response
            result = {"response": f"Something went wrong on our side ({exc}). Please try again.",
                      "next_step": action, "confidence": 0.0, "escalated": True, "data": {}}
        latency_ms = (time.perf_counter() - t0) * 1000

        # one trace per turn — summaries are PII-redacted before hitting disk
        summary_in = guardrails.sanitize_for_log(f"{action}:{str(context)[:120]}")
        summary_out = guardrails.sanitize_for_log(str(result.get("response", ""))[:120])
        log_trace(trace_id=trace_id, agent="manager_agent", input_summary=summary_in,
                  output_summary=summary_out, latency_ms=latency_ms)
        self.memory.add("user", summary_in)
        self.memory.add("manager", summary_out)
        return {"trace_id": trace_id, **result}

    # ── routing table (SECTION 7 flow) ────────────────────────────────
    async def _route(self, action: str, context: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
        handler = {
            "load_deck": self._load_deck,
            "start_quiz": self._start_quiz,
            "answer": self._answer,
            "hint": self._hint,
            "finish": self._finish,
            "ping": self._ping,
        }.get(action)
        if handler is None:
            return {"response": f"I don't know the action '{action}'. Try: load_deck, "
                                "start_quiz, answer, hint, finish.",
                    "next_step": None, "confidence": 0.0, "escalated": False, "data": {}}
        policy = policy_checker.check("manager_agent", {"action": action})
        if not policy["allowed"]:
            return {"response": f"Blocked by policy: {policy['reason']}",
                    "next_step": action, "confidence": 0.0, "escalated": False, "data": {}}
        return await handler(context, trace_id)

    async def _ping(self, context: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
        return {"response": "pong", "next_step": None, "confidence": 1.0,
                "escalated": False, "data": {"offline_mode": self.config.offline_mode}}

    async def _load_deck(self, context: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
        source: Union[str, bytes, Any] = context.get("file_source", "")
        outcome = await self.data_agent.run(file_source=source, file_format=context.get("file_format"))
        cards, errors = outcome["cards"], outcome["errors"]

        # Guardrail: deck content is UNTRUSTED input — injection text never becomes a card
        safe_cards: List[Flashcard] = []
        for card in cards:
            scan = guardrails.check_input(card.question + " " + card.answer)
            if not scan["safe"] and "prompt_injection_pattern" in scan["issues"]:
                errors.append(CardError(raw_text=card.question[:60],
                                        reason="Card rejected: its text looks like a prompt-"
                                               "injection attempt to hijack the quiz system."))
            else:
                safe_cards.append(card)
        cards = safe_cards
        if not cards:  # design doc §5: quiz agent is NEVER called — clean boundary
            msg = errors[0].reason if len(errors) == 1 else \
                "The file could not be loaded:\n" + "\n".join(
                    f"  • {e.reason}" + (f" (line {e.line_no})" if e.line_no else "") for e in errors)
            return {"response": f"Your deck didn't load — no quiz was started.\n\n{msg}",
                    "next_step": "load_deck", "confidence": 0.0, "escalated": False,
                    "data": {"cards": [], "errors": [e.model_dump() for e in errors]}}
        self.deck = cards
        self.engine = None
        note = f" ({len(errors)} item(s) skipped — see warnings)" if errors else ""
        return {"response": f"Deck ready: {len(cards)} flashcards{note}. "
                            "Choose a mode to start: sequential, random or adaptive.",
                "next_step": "start_quiz", "confidence": 1.0, "escalated": False,
                "data": {"cards": [c.model_dump() for c in cards],
                         "errors": [e.model_dump() for e in errors]}}

    async def _start_quiz(self, context: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
        if not self.deck:
            return {"response": "Load a deck first (upload a JSON/CSV/TXT card file).",
                    "next_step": "load_deck", "confidence": 0.0, "escalated": False, "data": {}}
        mode = str(context.get("mode", "sequential")).lower()
        if mode not in VALID_MODES:
            return {"response": f"Unknown mode '{mode}'. Choose: {', '.join(sorted(VALID_MODES))}.",
                    "next_step": "start_quiz", "confidence": 0.0, "escalated": False, "data": {}}
        self.engine = QuizEngine(self.deck, QuizMode(mode), trace_id=trace_id,
                                 seed=context.get("seed"))
        card = self.engine.next_card
        return {"response": f"Question 1/{len(self.deck)}: {card.question}",
                "next_step": "answer", "confidence": 1.0, "escalated": False,
                "data": {"card": card.model_dump(), "score": self.engine.score().model_dump()}}

    async def _answer(self, context: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
        if self.engine is None:
            return {"response": "No quiz in progress — load a deck and start a quiz first.",
                    "next_step": "load_deck", "confidence": 0.0, "escalated": False, "data": {}}
        given = str(context.get("answer", ""))
        scan = guardrails.check_input(given)
        if not scan["safe"] and "prompt_injection_pattern" in scan["issues"]:
            return {"response": "That input looks like a prompt-injection attempt, so it "
                                "wasn't graded. (PII in answers is auto-redacted in logs.)",
                    "next_step": "answer", "confidence": 0.0, "escalated": False, "data": {}}
        # PII is redacted before the answer reaches the grader AND the logs
        out = await self.engine.submit_answer(guardrails.redact_pii(given))
        verdict = out["verdict"]
        escalated = bool(verdict and (verdict.needs_review or verdict.confidence < 0.6))
        if out["finished"]:
            response = self.engine.results_summary()
        else:
            response = out["message"] + \
                       (f"\n\nNext — {out['next_card'].question}" if out["next_card"] else "")
        return {"response": response,
                "next_step": "finish" if out["finished"] else "answer",
                "confidence": verdict.confidence if verdict else 0.0,
                "escalated": escalated,
                "data": {"verdict": verdict.model_dump() if verdict else None,
                         "score": out["score"].model_dump(),
                         "finished": out["finished"],
                         "graded_by": verdict.graded_by if verdict else None}}

    async def _hint(self, context: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
        if self.engine is None or self.engine.next_card is None:
            return {"response": "No card in play to hint on.", "next_step": None,
                    "confidence": 0.0, "escalated": False, "data": {}}
        result = await self.engine.hint()
        data = result.get("data") or {}
        return {"response": data.get("hint_text", "No hint available right now."),
                "next_step": "answer", "confidence": 1.0, "escalated": False,
                "data": {"hint": data}}

    async def _finish(self, context: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
        if self.engine is None:
            return {"response": "Nothing to finish yet.", "next_step": None,
                    "confidence": 0.0, "escalated": False, "data": {}}
        summary = self.engine.results_summary()
        self.engine = None
        return {"response": summary, "next_step": None, "confidence": 1.0,
                "escalated": False, "data": {"deck_size": len(self.deck)}}

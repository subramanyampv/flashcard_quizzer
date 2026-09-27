"""Data Agent — reads & checks flashcard files (design doc §3, PLAN §3).

Wraps the load_flashcard_file tool and enforces the Pydantic Flashcard
contract. Output is ALWAYS a (cards, errors) pair: broken files produce
friendly CardError objects and an empty card list, so the Manager can show a
banner and the Quiz Agent is never invoked on dirty data (design doc §5).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import ValidationError

from agent.schemas import CardError, Flashcard
from tools.implementations.load_flashcard_file import load_flashcard_file


class DataAgent:
    """Validating file loader: raw source in → clean list[Flashcard] out."""

    name = "data_agent"

    async def run(
        self, file_source: Union[str, bytes, Any], file_format: Optional[str] = None
    ) -> Dict[str, Union[List[Flashcard], List[CardError]]]:
        """Parse + validate a deck. Never raises; failures land in `errors`."""
        result = await load_flashcard_file(source=file_source, format=file_format or "")
        cards: List[Flashcard] = []
        errors: List[CardError] = []

        if result["status"] == "error":
            errors.append(CardError(reason=result["error_msg"] or "The file could not be read."))
            return {"cards": cards, "errors": errors}

        for entry in result["data"].get("parse_errors", []):
            errors.append(CardError(line_no=entry.get("line_no"),
                                    raw_text=entry.get("raw_text"),
                                    reason=entry["reason"]))

        seen_questions: Dict[str, Flashcard] = {}
        for raw in result["data"]["cards"]:
            question, answer = str(raw.get("question", "")).strip(), str(raw.get("answer", "")).strip()
            where = f"line {raw['line_no']}" if raw.get("line_no") else f"card #{raw.get('raw_index', 0) + 1}"
            missing = []
            if not question:
                missing.append("a question")
            if not answer:
                missing.append("an answer")
            if missing:
                errors.append(CardError(
                    line_no=raw.get("line_no"), raw_text=(question or answer)[:80],
                    reason=f"The {where} entry is missing {' and '.join(missing)}. "
                           "Every card needs both a question and an answer."))
                continue
            dedupe_key = question.lower()
            if dedupe_key in seen_questions:  # merge duplicates (agent prompt contract)
                errors.append(CardError(
                    line_no=raw.get("line_no"), raw_text=question[:80],
                    reason=f"Duplicate of an earlier card ('{question[:40]}…') — kept the first one."))
                continue
            try:
                card = Flashcard(
                    id=str(raw.get("id") or ""), question=question, answer=answer,
                    tags=list(raw.get("tags") or []), difficulty=raw.get("difficulty") or "medium",
                )
            except ValidationError as exc:  # id is required; generate if parser missed it
                fixed = {**raw, "question": question, "answer": answer}
                if not str(fixed.get("id", "")):
                    from tools.implementations.load_flashcard_file import _make_id
                    fixed["id"] = _make_id(question)
                try:
                    card = Flashcard(**fixed)
                except ValidationError as vexc:
                    errors.append(CardError(line_no=raw.get("line_no"), raw_text=question[:80],
                                            reason=f"The {where} card is malformed: {vexc.errors()[0]['msg']}"))
                    continue
            seen_questions[dedupe_key] = card
            cards.append(card)

        if not cards and not errors:
            errors.append(CardError(reason="No flashcards found in the file. "
                                           "Expected at least one question/answer pair."))
        return {"cards": cards, "errors": errors}

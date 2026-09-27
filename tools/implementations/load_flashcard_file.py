"""load_flashcard_file tool — multi-format deck parser (PLAN §4.1).

Handles JSON (array | wrapped object), CSV, and alternating-line TXT/MD
(line 1 = question, line 2 = answer, blank-line separated). Parsing never
raises: every failure becomes a structured error entry the Data Agent turns
into a friendly CardError. Business validation stays in data_agent.py.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Flexible field aliases learners tend to use
_Q_KEYS = ("question", "q", "front", "prompt", "term")
_A_KEYS = ("answer", "a", "back", "response", "definition")


def _make_id(question: str) -> str:
    return hashlib.sha1(question.strip().lower().encode("utf-8")).hexdigest()[:12]


def _pick(d: Dict[str, Any], keys: tuple) -> str:
    lowered = {str(k).strip().lower(): v for k, v in d.items() if k is not None}
    for key in keys:
        if key in lowered and lowered[key] is not None:
            return str(lowered[key]).strip()
    return ""


def _parse_json(text: str) -> tuple[List[dict], List[dict]]:
    cards, errors = [], []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        errors.append({"line_no": exc.lineno, "raw_text": None,
                       "reason": f"The file is not valid JSON ({exc.msg} at line {exc.lineno}). "
                                 "Check for a missing comma, bracket or quote."})
        return cards, errors
    if isinstance(payload, dict):
        for wrapper in ("cards", "flashcards", "data", "deck"):
            if isinstance(payload.get(wrapper), list):
                payload = payload[wrapper]
                break
        else:
            payload = [payload]  # single card in a bare object
    if not isinstance(payload, list):
        errors.append({"line_no": None, "raw_text": None,
                       "reason": "Expected a list of cards or a {'cards': [...]} object."})
        return cards, errors
    for i, item in enumerate(payload):
        if not isinstance(item, dict):
            errors.append({"line_no": None, "raw_text": str(item)[:80],
                           "reason": f"Card #{i + 1} is not a JSON object."})
            continue
        q, a = _pick(item, _Q_KEYS), _pick(item, _A_KEYS)
        tags = item.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        cards.append({"id": str(item.get("id") or _make_id(q or f"card-{i}")),
                      "question": q, "answer": a, "tags": list(tags),
                      "difficulty": item.get("difficulty"), "raw_index": i})
    return cards, errors


def _parse_csv(text: str) -> tuple[List[dict], List[dict]]:
    cards, errors = [], []
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        errors.append({"line_no": None, "raw_text": None, "reason": "The CSV file has no header row."})
        return cards, errors
    norm = {h: (str(h).strip().lower() if h else h) for h in reader.fieldnames}
    if not (set(norm.values()) & set(_Q_KEYS)) or not (set(norm.values()) & set(_A_KEYS)):
        errors.append({"line_no": None,
                       "raw_text": ",".join(reader.fieldnames),
                       "reason": "CSV header must include a question column (question/q/front) "
                                 "and an answer column (answer/a/back)."})
        return cards, errors
    for i, row in enumerate(reader):
        renamed = {norm.get(k, k): v for k, v in row.items() if k is not None}
        q, a = _pick(renamed, _Q_KEYS), _pick(renamed, _A_KEYS)
        tags_raw = str(renamed.get("tags", "") or "")
        cards.append({"id": str(renamed.get("id") or _make_id(q or f"row-{i}")),
                      "question": q, "answer": a,
                      "tags": [t.strip() for t in tags_raw.split(";") if t.strip()],
                      "difficulty": renamed.get("difficulty"), "raw_index": i,
                      "line_no": i + 2})
    return cards, errors


_Q_PREFIX = re.compile(r"^\s*(?:q|question|front)[:)\-\s]\s*", re.IGNORECASE)
_A_PREFIX = re.compile(r"^\s*(?:a|answer|back)[:)\-\s]\s*", re.IGNORECASE)


def _parse_txt(text: str) -> tuple[List[dict], List[dict]]:
    cards, errors = [], []
    pending_q: Optional[str] = None
    pending_line: Optional[int] = None
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("---") or line.startswith("==="):
            continue
        has_q_prefix = bool(_Q_PREFIX.match(line))
        if pending_q is None:
            if _A_PREFIX.match(line):  # answer with nothing to answer
                errors.append({"line_no": line_no, "raw_text": line[:80],
                               "reason": "An answer line appeared before its question."})
                continue
            pending_q, pending_line = _Q_PREFIX.sub("", line).strip(), line_no
        elif has_q_prefix:  # two questions in a row → previous one dangles
            errors.append({"line_no": pending_line, "raw_text": pending_q[:80],
                           "reason": "A question line had no answer line before the next question."})
            pending_q, pending_line = _Q_PREFIX.sub("", line).strip(), line_no
        else:  # any other line (bare or A:-prefixed) completes the pair
            answer = _A_PREFIX.sub("", line).strip()
            if answer:
                cards.append({"id": _make_id(pending_q), "question": pending_q, "answer": answer,
                              "tags": [], "difficulty": None, "raw_index": len(cards),
                              "line_no": pending_line})
            else:
                errors.append({"line_no": line_no, "raw_text": pending_q[:80],
                               "reason": "Answer line was empty after stripping."})
            pending_q, pending_line = None, None
    if pending_q is not None:
        errors.append({"line_no": pending_line, "raw_text": pending_q[:80],
                       "reason": "File ends with a question that has no answer."})
    return cards, errors


async def load_flashcard_file(source: Union[str, bytes, io.IOBase], format: str = "") -> Dict[str, Any]:
    """Parse a deck from a path, bytes, or readable stream. Never raises for bad input."""
    try:
        fmt = (format or "").strip().lower().lstrip(".")
        name = ""
        if isinstance(source, (bytes, bytearray)):
            data = source.decode("utf-8", errors="replace")
        elif isinstance(source, str):
            path = Path(source)
            if not path.is_file():
                return {"status": "error", "data": {"cards": [], "warnings": []},
                        "error_msg": f"I could not find the file '{source}'. Check the path and try again."}
            name, data = path.name, path.read_text(encoding="utf-8", errors="replace")
        else:  # stream-like (e.g. Streamlit upload)
            raw = source.read()
            data = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
            name = getattr(source, "name", "")

        if not fmt:  # auto-detect by extension then content
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            fmt = {"json": "json", "csv": "csv", "txt": "txt", "md": "md"}.get(ext, "")
        if not fmt:
            stripped = data.lstrip()
            fmt = "json" if stripped[:1] in "[{" else "csv" if "," in data.splitlines()[0] else "txt"

        parser = {"json": _parse_json, "csv": _parse_csv, "md": _parse_txt}.get(fmt, _parse_txt)
        if fmt not in ("json", "csv", "md", "txt"):
            return {"status": "error", "data": {"cards": [], "warnings": []},
                    "error_msg": f"Unsupported file type '.{fmt}'. Use json, csv, txt or md."}
        cards, errors = parser(data)
        return {"status": "ok", "data": {"cards": cards, "parse_errors": errors, "format": fmt},
                "error_msg": None}
    except Exception as exc:  # last-resort boundary: still a structured error, never a crash
        return {"status": "error", "data": {"cards": [], "warnings": []},
                "error_msg": f"Unexpected problem reading the file: {exc}"}

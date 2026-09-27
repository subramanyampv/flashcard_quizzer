"""Safety guardrails: PII redaction, prompt-injection detection, toxicity check.

Every input reaching the agents (learner answers AND uploaded deck text —
untrusted!) is scanned first. Toggles come from config settings so the whole
layer can be switched off for local experiments (blueprint §4 safety).
"""
from __future__ import annotations

import re
from typing import Dict, List

from config import get_config

PII_PATTERNS: Dict[str, re.Pattern] = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "phone": re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?!\d)"),
    "ssn": re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    "credit_card": re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
}

# Patterns that try to hijack an LLM's system prompt
INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(all\s+|the\s+)?previous\s+instructions",
        r"disregard\s+(all\s+)?(your|the|previous)\s+(instructions|rules|prompt)",
        r"reveal\s+(your\s+)?(system\s+)?prompt",
        r"you\s+are\s+now\s+(dan|jailbroken|unrestricted)",
        r"pretend\s+(that\s+)?you\s+have\s+no\s+(rules|restrictions)",
        r"system\s*:\s*",
    )
]

TOXICITY_KEYWORDS = {
    "hate speech": (r"\b(hate\s+speech|slur|bigot)\b",),
    "violence": (r"\b(kill\s+yourself|harm\s+(yourself|others))\b",),
    "abuse": (r"\b(idiot|moron|stupid\s+bitch|shut\s+up\s+idiot)\b",),
}


def _toggles() -> Dict[str, bool]:
    try:
        s = get_config().settings
        return {"pii": s.safety_block_pii, "injection": s.safety_block_prompt_injection,
                "toxicity": s.safety_check_output_toxicity}
    except Exception:
        return {"pii": True, "injection": True, "toxicity": True}


def find_pii(text: str) -> List[str]:
    return sorted({kind for kind, rx in PII_PATTERNS.items() if rx.search(text or "")})


def redact_pii(text: str) -> str:
    out = text or ""
    for kind, rx in PII_PATTERNS.items():
        out = rx.sub(f"[REDACTED_{kind.upper()}]", out)
    return out


def has_injection(text: str) -> bool:
    return any(rx.search(text or "") for rx in INJECTION_PATTERNS)


def _toxic_categories(text: str) -> List[str]:
    found = []
    for cat, patterns in TOXICITY_KEYWORDS.items():
        if any(re.search(p, text or "", re.IGNORECASE) for p in patterns):
            found.append(cat)
    return found


def check_input(text: str) -> Dict[str, object]:
    """Scan learner/UI input. safe=False must stop the pipeline gracefully."""
    issues: List[str] = []
    flags = _toggles()
    if flags["injection"] and has_injection(text):
        issues.append("prompt_injection_pattern")
    if flags["pii"]:
        pii = find_pii(text)
        issues.extend(f"pii:{kind}" for kind in pii)
    return {"safe": not issues, "issues": issues, "text": text or ""}


def check_output(text: str) -> Dict[str, object]:
    """Scan anything we're about to show or log: PII must not leak, toxicity flags."""
    issues: List[str] = []
    flags = _toggles()
    if flags["pii"]:
        pii = find_pii(text)
        if pii:
            issues.extend(f"pii:{kind}" for kind in pii)
    if flags["injection"] and has_injection(text):
        issues.append("prompt_injection_pattern")
    if flags["toxicity"]:
        issues.extend(f"toxicity:{c}" for c in _toxic_categories(text))
    return {"safe": not issues, "issues": issues, "text": text or ""}


def sanitize_for_log(text: str) -> str:
    """Always run before writing to logs — PII never reaches disk (constraint)."""
    return redact_pii(text) if _toggles()["pii"] else (text or "")

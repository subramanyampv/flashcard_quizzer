# Agent Design (blueprint Section 2 + design doc §2–4)

## Roles
| Agent | Module | Kind | Owns | Never does |
| --- | --- | --- | --- | --- |
| Manager | `agent/core_agent.py` | runtime | routing, guardrails, policy checks, tracing, session state | parsing files, grading |
| Data | `agent/agents/data_agent.py` | runtime | multi-format loading, validation, dedupe, friendly `CardError`s | quiz logic, UI |
| Quiz | `agent/agents/quiz_agent.py` | runtime | modes (sequential/random/adaptive), re-queue cap, score/streak, hints | file access, rendering |
| UI | `ui/app.py`, `ui/cli.py` | runtime | Streamlit + terminal surfaces, feedback widget | any business/scoring logic |
| QA | `agent/agents/qa_agent.py` + `tests/` + `evaluation/` | gate | pytest + grading-agreement harness; PASS is the release condition | shipping |

## Flow (hierarchical)
```
Learner ⇄ UI ⇄ Manager ──load_deck──► DataAgent ──list[Flashcard]+errors──► Manager
                         ──answer/hint──► QuizEngine ──Verdict+Score──► Manager ──► UI
                                 │
                                 └── tools/tool_registry ─► grade_answer / generate_hint (OpenAI), track_stats, log_trace
```
Runtime turn sequence diagram + failure-path table live in PLAN.md §1.

## Boundaries enforced by tests
- Bad upload → friendly banner, `QuizEngine` never constructed (`test_integration.test_bad_file_scenario`)
- Deck text is untrusted: injection patterns rejected at load (`test_injection_in_deck_rejected`)
- PII redacted before LLM calls AND logs (`test_pii_redacted_in_logs`)
- LLM outage → one retry → keyword fallback → quiz still completes (`test_llm_failure_retries_then_falls_back`)
- Adaptive convergence: re-queue cap 2 guarantees termination (`test_adaptive_three_straight_misses_becomes_final`)

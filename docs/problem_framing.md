# Problem Framing (blueprint Section 1)

**Problem.** Learners collect flashcards in ad-hoc files (JSON decks, CSV exports,
text lists) but have no reliable way to self-test: existing apps demand their
proprietary formats, crash on malformed input, and only check exact string matches
— so a semantically correct answer gets marked wrong.

**Why multi-agent.** One monolithic prompt produces brittle code and conflates
concerns (parsing, grading, rendering). Splitting into Manager / Data / Quiz / UI /
QA agents (design doc §1–2) means each module has one owner, boundaries are
testable ("the quiz agent is NEVER called with dirty data"), and features extend
one file (a new quiz mode touches only the Quiz Agent).

**Why OpenAI-first with strict validation.** Every LLM call is graded/hint
generation; a wrong key or unavailable model should halt the app with a clear
message at startup (3-tier gate in `config.py`), not produce a half-working quiz.
The same stack must work with zero key (deterministic fallback grading), which
forces clean dependency boundaries anyway.

**Success metrics** (measured by `scripts/test_run.py` + eval harness):
- 100% of malformed files → friendly structured errors, zero unhandled exceptions (tests/test_data_agent.py, test_integration.py)
- All 3 modes complete end-to-end; adaptive re-queues ≤2× then records a final miss (tests/test_quiz_agent.py)
- Grading agreement ≥90% on the 6-case rubric (currently 100% on fallback; rubric chosen to be valid for both grading modes)
- Offline mode fully functional without a key; live mode activates by editing `.env` only

**Users.** Students, technical learners, exam preppers; single local user, no auth.
**Constraints.** OpenAI-only provider, keys never outside `.env`, no external DB,
local venv for all Python.

# Flashcard Quizzer — Multi-Agent Study System

> Load your own flashcard files, quiz yourself in three modes, and get **AI-graded
> free-text answers** with semantic (not exact-string) matching, partial credit,
> progressive hints, and adaptive re-queueing of what you miss.

The system is built as a **team of cooperating agents** instead of one monolith:

| Agent | Lives in | Job |
| --- | --- | --- |
| 🧑‍💼 **Manager** | `agent/core_agent.py` | The only door the UI uses. Routes actions, enforces safety & policy, traces every turn |
| 📂 **Data** | `agent/agents/data_agent.py` | Parses JSON/CSV/TXT/MD decks, validates every card, turns corruption into friendly errors — never crashes |
| 🧠 **Quiz** | `agent/agents/quiz_agent.py` | Sequential / Random / **Adaptive** modes, scoring & streaks, LLM grading, progressive hints |
| 🖥️ **UI** | `ui/app.py`, `ui/cli.py` | Streamlit web app + colorized terminal interface. Zero business logic |
| ✅ **QA** | `agent/agents/qa_agent.py` + `tests/` + `evaluation/` | Owns 41 tests and the grading-quality harness. The project is "done" only when QA says PASS |

```
Learner ⇄ UI ⇄ Manager ──files──► Data Agent ──list[Flashcard]──► Manager
                       └─answers─► Quiz Agent ──Verdict+Score──► UI
                                   │
                                   └─► tools/ ─► OpenAI (grade, hint) · track_stats · log_trace
```

**Docs:** [PLAN.md](PLAN.md) (master architecture) · [docs/problem_framing.md](docs/problem_framing.md) ·
[docs/agent_design.md](docs/agent_design.md) · [docs/evaluation_report.md](docs/evaluation_report.md) ·
[docs/engineering_decisions.md](docs/engineering_decisions.md)

---

## Contents
1. [Quickstart](#quickstart)
2. [Configuration & LLM access validation](#configuration--llm-access-validation)
3. [Running the app](#running-the-app)
4. [Deck formats](#deck-formats)
5. [Quiz modes & grading](#quiz-modes--grading)
6. [Project layout](#project-layout)
7. [Testing & QA gates](#testing--qa-gates)
8. [Observability & cost](#observability--cost)
9. [Safety & privacy](#safety--privacy)
10. [Troubleshooting](#troubleshooting)
11. [Docker](#docker)

---

## Quickstart

**Requirements:** macOS/Linux, Python 3.11+ (built & tested on 3.12).
All Python runs inside the project's local virtualenv — never globally.

```bash
cd q-proj
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then edit .env (see next section)
```

That's it — the quizzer **works with no API key at all** (offline mode:
deterministic keyword grading). Add a key when you want true semantic grading.

## Configuration & LLM access validation

Everything configurable lives in `.env`; **no module reads `os.environ` or hardcodes
secrets**. `config.py` validates in 3 tiers and every entrypoint (web, CLI, eval)
runs the gate before doing anything else:

```
Tier 1 (static)   : .env exists · key matches ^sk-…{20,} · temperature 0–2 · max_tokens 1–4096
Tier 2 (live)     : models.list() proves auth+network · a 5-token ping proves MODEL access
Tier 3 (gate)     : startup table; the app halts with remediation steps on failure
```

### `.env` reference

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPENAI_API_KEY` | — | The **only** secret. Displayed as `sk-…{last4}` everywhere, never in logs |
| `OPENAI_MODEL` | `gpt-4o-mini` | Any chat model your endpoint allows (OpenAI, or OpenAI-compatible via `OPENAI_BASE_URL`) |
| `OPENAI_TEMPERATURE` | `0.3` | Grading wants consistency, not creativity |
| `OPENAI_MAX_TOKENS` | `400` | Caps per-call output (grading ≤300, hints ≤120) |
| `OPENAI_BASE_URL` | *(empty)* | Set to use any OpenAI-compatible endpoint instead of `api.openai.com` |
| `LLM_VALIDATE_ON_STARTUP` | `true` | `false` = **supported offline mode**: static checks only, tools use the keyword fallback |
| `LOG_LEVEL` | `INFO` | |
| `LANGFUSE_PUBLIC_KEY` / `SECRET_KEY` / `HOST` | *(empty)* | Set → traces go to Langfuse; empty → local JSON-lines |
| `SAFETY_BLOCK_PII` | `true` | Redact emails/phones/SSN/cards before grading **and** logging |
| `SAFETY_BLOCK_PROMPT_INJECTION` | `true` | Reject hijack-phrase patterns in deck content & answers |
| `SAFETY_CHECK_OUTPUT_TOXICITY` | `true` | Keyword scan on rendered output |

### Verify your setup any time

```bash
.venv/bin/python config.py --check
```
```
[✓] Configuration File (.env)               : …/.env
[✓] OpenAI API Key Format                   : sk-…xxxx
[✓] OpenAI API Connectivity                 : AUTHENTICATED
[✓] Model '<your-configured-model>' Access   : OPERATIONAL (Latency: 612ms)
[!] Observability Backend                   : LOCAL FALLBACK (logs/interactions.log)
Status: LIVE LLM READY — optional integrations missing (see ! lines).
```

## Running the app

### Web (Streamlit) — `http://localhost:8501`
```bash
.venv/bin/streamlit run ui/app.py
```
Pick a sample deck or upload your own → choose a mode → flip cards, answer in
free text, watch the progress bar, thumbs-up/down the last verdict. Sidebar shows
trace id, confidence, escalation flags, deck load warnings, and the startup health
table. Commands in the chat box: `/hint` · `/skip` · `/start <mode>` · `/reset`.

### Terminal (CLI)
```bash
.venv/bin/python -m ui.cli --file data/sample_decks/python_basics.json --mode adaptive
```
| Flag | Purpose |
| --- | --- |
| `--file` | deck path (json/csv/txt/md) |
| `--format` | force parser: `json\|csv\|txt\|md` (auto-detected otherwise) |
| `--mode` | start directly in `sequential\|random\|adaptive` |
| `--seed` | deterministic shuffle (testing) |

In-prompt commands: `load <path>` · `hint` · `skip` · `results` · `help` · `quit`.
The CLI is pipeable: `printf 'Paris\nskip\nquit\n' | .venv/bin/python -m ui.cli …`

## Deck formats

| Format | Accepted shape |
| --- | --- |
| **JSON** | `[{ "question", "answer", "tags"?, "difficulty"? }]` · wrapped `{"cards": [...]}` · alias keys `q/front/prompt`, `a/back/response/definition` · tags as list or comma string |
| **CSV** | header row with question & answer columns (+ optional `tags` — semicolon-separated — and `difficulty`) |
| **TXT/MD** | alternating lines: question, answer, blank line between pairs; `Q:`/`A:` prefixes optional; `#` comments skipped |

Three sample decks ship in `data/sample_decks/` (Python, System Design, Data Structures).
**Failure contract:** every malformed file produces per-line `CardError` messages and
the quiz engine is *never invoked* on dirty data — no unhandled exceptions, ever.

## Quiz modes & grading

| Mode | Behavior |
| --- | --- |
| **Sequential** | deck order as loaded; each card once |
| **Random** | shuffled once up front; each card once |
| **Adaptive** | shuffled; missed / partial / low-confidence cards are **re-queued at most 2×**, then recorded as a final miss — guaranteed convergence, no infinite loops |

**Grading (`grade_answer` tool → OpenAI):** semantic comparison, JSON-enforced verdict
`{correct, partial, explanation, confidence, needs_review}`. Synonyms, reordering and
extra correct context pass; answers omitting a core element get **partial credit**.
*Failure policy:* one retry → deterministic keyword/overlap fallback (`graded_by:
fallback-keyword`), so a quiz always finishes even during an API outage.
**Hints (`generate_hint`):** attempt 1 = terse nudge, attempt 2 = fuller context, never
the answer (a leak-check masks it if the model slips).

## Project layout

```
q-proj/
├── config.py                  # Pydantic BaseSettings + 3-tier validation + health table
├── agent/
│   ├── core_agent.py          # Manager Agent (orchestrator, safety, policy, tracing)
│   ├── schemas.py             # Pydantic contracts: Flashcard, Verdict, Score, TraceEvent…
│   ├── prompts.py             # ALL LLM persona text, versioned in one place
│   ├── memory.py              # session turns + long-term feedback view
│   └── agents/                # data_agent · quiz_agent · qa_agent
├── llm/openai_client.py       # the single validated client — no module touches the key
├── tools/                     # tool_registry (async call_tool) + implementations/
├── safety/guardrails.py       # PII · prompt-injection · toxicity
├── monitoring/                # Langfuse or JSON-lines + USD cost table
├── policy_rlhf/               # policy.json enforcement · feedback_store
├── evaluation/                # deepeval harness + custom metrics
├── ui/                        # app.py (Streamlit) · cli.py · components · state
├── data/                      # sample_decks · policy · evaluation rubric · stats · feedback
├── fixtures/ + tests/         # bad-file fixtures + 41 tests
├── scripts/test_run.py        # 4-stage pipeline gate (config→quiz→boundary→QA)
└── deployment/                # Dockerfile + docker-compose.yml
```

## Testing & QA gates

```bash
.venv/bin/python -m pytest -q                     # 41 tests, deterministic w/ or w/o a key
.venv/bin/python -m evaluation.deepeval_harness   # grading-agreement eval (6-case rubric)
.venv/bin/python scripts/test_run.py              # the full manager→data→quiz→QA pipeline
```

| Gate | What it proves | Status |
| --- | --- | --- |
| Unit + integration (41) | validation tiers, all formats & corruption paths, 3 modes + re-queue cap, bad-file boundary (§5 of the design doc), PII never in logs, LLM retry→fallback | ✅ |
| Grading eval (threshold ≥90%) | verdict agreement vs `data/evaluation/eval_rubric.json` — **passes in both modes** | ✅ 6/6 offline · 6/6 live |
| `test_run.py` | all four stages green in one run | ✅ |

Tests stay deterministic by pinning LLM state in `tests/conftest.py` — your live key
never changes test behavior. Add rubric cases to `eval_rubric.json` to extend the eval.

## Observability & cost

Every Manager turn emits one `TraceEvent` (agent, tool, latency, token counts,
**USD cost**, error) → `logs/interactions.log` (JSON lines), errors → `logs/errors.log`,
eval runs → `logs/eval.log`. With Langfuse keys set, the same events become traces
(spans + generations) with native cost. Local pricing lives in
`monitoring/langfuse_logger.py::PRICING` (gpt-4o-mini priced; add your model's row to
see per-session cost — unknown models simply report no cost).

## Safety & privacy

- **Key hygiene:** `.env` is git-ignored; the key only ever appears masked. No telemetry without your config.
- **Untrusted input:** uploaded decks and typed answers pass injection & PII scans before any LLM call; PII is redacted before logging too.
- **No external database:** stats/feedback/policy are JSON files on disk.
- **Bounded cost:** tiny grading max_tokens, one retry policy, 2-requeue adaptive cap.

## Troubleshooting

| Symptom / message | Meaning & fix |
| --- | --- |
| `No .env found … cp .env.example .env` | Create `.env` as prompted |
| `OPENAI_API_KEY is empty but LLM_VALIDATE_ON_STARTUP=true` | Add a key **or** accept offline mode by setting the flag to `false` |
| `does not look like a valid OpenAI key` | Re-copy the key; watch for quotes/truncation/leading spaces |
| `was REJECTED by OpenAI` | Wrong/revoked key — new one at the provider dashboard |
| `Model 'X' is not accessible` | Key is fine but lacks that model — set `OPENAI_MODEL` (or `OPENAI_BASE_URL` for a compatible provider) |
| `No network route to …` | Firewall/VPN/proxy; or point `OPENAI_BASE_URL` at your gateway |
| `! Observability Backend: LOCAL FALLBACK` | Normal — Langfuse is optional; traces still in `logs/interactions.log` |
| Deck refuses to load | Sidebar shows the exact `CardError` line/reason; fix the file (see formats) — nothing ever crashes |
| Answers graded "offline by keyword fallback" despite a key | API hiccup → retry → fallback by design; check `logs/errors.log` |

## Docker

```bash
docker compose -f deployment/docker-compose.yml up --build
```
Serves the Streamlit UI on `8501`, injects `.env` at runtime (never baked into the
image), and mounts `logs/`, `knowledge/`, `data/` back to the host.

---

*Built from the blueprint in `PLAN.md`, merging `MULTI_AGENT_BLUEPRINT-2.md`
(structure & contracts) with the flashcard design doc (domain logic) — provider
pinned to OpenAI with strict `.env` validation as the project decision.*

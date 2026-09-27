# Engineering Decisions (trade-off log)

| # | Decision | Alternative rejected | Why |
| --- | --- | --- | --- |
| 1 | OpenAI-only via one shared, config-fed client (`llm/openai_client.py`) | blueprint's dual anthropic/openai | user requirement; single secret path; provider swap stays one module |
| 2 | 3-tier eager validation + startup gate; `LLM_VALIDATE_ON_STARTUP=false` = supported offline mode with keyword-fallback grading | hard-fail without key | lets tests + demo run anywhere; with a key the gate fails fast with remediation text instead of half-working |
| 3 | Pydantic everywhere: `BaseSettings` + schemas.py contracts at every boundary | bare dicts (blueprint literal) | PLAN.md Pillar 2; type errors surface at load, not mid-quiz |
| 4 | Retry-once → deterministic fallback for LLM grading | escalate to human | quiz completion > grading sophistication for self-study; needs_review flag keeps adaptive mode honest |
| 5 | Keyword scorer = token coverage (stopword-stripped) + difflib similarity, thresholds .55/.2 | embeddings for offline grading | zero deps, deterministic, testable; embeddings only arrive with optional FAISS step |
| 6 | No external DB; session stats as JSON files (`data/stats/`) | SQLite | blueprint constraint; file store is inspectable and git-ignored |
| 7 | Langfuse optional & guarded import; JSON-lines fallback with local pricing table | mandatory Langfuse | observability must never crash the app or require accounts |
| 8 | MCP layer deferred; `tool_registry.call_tool(name, params)` keeps the client shape | build `mcp/server.py` now | in-process tools + async envelope = drop-in MCP wrapper later without touching agents |
| 9 | Deck files treated as untrusted input (injection scan, PII redaction before grading AND logging) | trust uploaded content | prompt-injection via a shared CSV deck is the realistic attack for this app |
| 10 | Guardrails live in code toggled by Settings (`SAFETY_*` env flags) | runtime-configurable policy engine | blueprint §4 scope; policy.json remains declarative and enforced at its 3 machine-checkable points |
| 11 | Step 8 (FAISS "explain from notes") skipped: heavy deps (torch/faiss) for a disabled-by-default feature | include it | knowledge_base.enabled=false in the plan; keep install light |
| 12 | Adaptive re-queue cap = exactly 2, then final miss | until-correct loop | plan metric; guarantees termination (property tested) |
| 13 | `ui/cli.py` before `ui/app.py` | web first | design-doc handoff order; CLI is scriptable for gate runs (`printf answers | python -m ui.cli`) |
| 14 | Tests pin LLM state via conftest (`offline_llm` autouse) | rely on dev `.env` | suite is deterministic whether or not a live key exists |

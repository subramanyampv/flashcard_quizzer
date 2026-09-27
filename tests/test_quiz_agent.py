"""Step 4 gate — Quiz Agent: 3 modes, scoring math, adaptive 2-requeue cap,
hints, persistence, and the LLM→fallback failure policy."""
from __future__ import annotations

import pytest

from agent.agents.quiz_agent import MAX_REQUEUES, QuizEngine
from agent.schemas import QuizMode


async def run_answers(engine: QuizEngine, answers):
    last = None
    for a in answers:
        last = await engine.submit_answer(a)
    return last


# ── sequential ─────────────────────────────────────────────────────────
async def test_sequential_order_and_completion(cards):
    eng = QuizEngine(cards, QuizMode.SEQUENTIAL, trace_id="t-seq")
    assert eng.next_card.id == "c1"
    r1 = await eng.submit_answer("Paris")
    assert r1["verdict"].correct and r1["next_card"].id == "c2"
    await eng.submit_answer("four")
    r3 = await eng.submit_answer("Jupiter")
    assert r3["finished"] and r3["next_card"] is None
    s = eng.score()
    assert (s.answered, s.correct, s.accuracy) == (3, 3, 1.0)


async def test_semantic_keyword_grading_accepts_variants(cards):
    eng = QuizEngine(cards, QuizMode.SEQUENTIAL, trace_id="t-kw")
    r = await eng.submit_answer("the city of paris, city of light")
    assert r["verdict"].correct and r["verdict"].graded_by == "fallback-keyword"


async def test_wrong_answer_records_final_miss_in_sequential(cards):
    eng = QuizEngine(cards, QuizMode.SEQUENTIAL, trace_id="t-seq-miss")
    await eng.submit_answer("I like cats")  # no overlap → wrong
    s = eng.score()
    assert s.correct == 0 and [c.id for c in s.missed_cards] == ["c1"]
    assert eng.next_card.id == "c2"  # not re-queued outside adaptive


async def test_empty_and_skip_do_not_break_state(cards):
    eng = QuizEngine(cards, QuizMode.RANDOM, trace_id="t-empty", seed=1)
    r = await eng.submit_answer("   ")
    assert r["verdict"] is None and eng.score().answered == 0
    r = await eng.submit_answer("skip")
    assert r["verdict"] is not None and eng.score().answered == 1


# ── random ─────────────────────────────────────────────────────────────
async def test_random_with_seed_asks_every_card_exactly_once(cards):
    eng = QuizEngine(cards, QuizMode.RANDOM, trace_id="t-rand", seed=42)
    order = [eng.next_card.id]
    answers = {"c1": "Paris", "c2": "four", "c3": "Jupiter"}
    while not eng.finished:
        r = await eng.submit_answer(answers[eng.next_card.id])
        if eng.next_card:
            order.append(eng.next_card.id)
    assert sorted(order) == ["c1", "c2", "c3"] and len(order) == 3
    assert eng.score().correct == 3


# ── adaptive: the 2-requeue cap is the design-doc core claim ──────────
async def test_adaptive_requeues_missed_card_max_twice(cards):
    eng = QuizEngine(cards, QuizMode.ADAPTIVE, trace_id="t-adapt", seed=7)
    # answer c1's question wrong, right when it comes back, then finish others
    asked_wrong = 0
    while not eng.finished:
        card = eng.next_card
        if card.id == "c1" and asked_wrong < 2:
            await eng.submit_answer("definitely unknown")
            asked_wrong += 1
        elif card.id == "c1":
            await eng.submit_answer("Paris")  # mastered on 3rd attempt
        else:
            await eng.submit_answer({"c2": "four", "c3": "Jupiter"}[card.id])
    assert eng._requeues["c1"] == MAX_REQUEUES  # requeued exactly twice
    assert "c1" not in eng._final_missed       # and then got it right
    assert eng.score().answered == 5           # c1 asked 3 times + c2 + c3


async def test_adaptive_three_straight_misses_becomes_final(cards):
    eng = QuizEngine(cards, QuizMode.ADAPTIVE, trace_id="t-adapt-miss", seed=3)
    guard = 0
    while not eng.finished and guard < 50:
        guard += 1
        card = eng.next_card
        ans = "cat" if card.id == "c1" else {"c2": "four", "c3": "Jupiter"}[card.id]
        await eng.submit_answer(ans)
    assert guard < 50, "adaptive mode looped forever — cap broken"
    assert eng._requeues.get("c1", 0) == MAX_REQUEUES
    assert [c.id for c in eng.score().missed_cards] == ["c1"]


async def test_adaptive_reduces_repeat_misses(cards):
    """First pass wrong on every card; second pass right → no final misses."""
    eng = QuizEngine(cards, QuizMode.ADAPTIVE, trace_id="t-adapt-good", seed=11)
    correct = {"c1": "Paris", "c2": "four", "c3": "Jupiter"}
    first_pass = set()
    while not eng.finished:
        card = eng.next_card
        await eng.submit_answer("nope" if card.id not in first_pass else correct[card.id])
        first_pass.add(card.id)
    assert eng.score().missed_cards == []


# ── scoring math ───────────────────────────────────────────────────────
async def test_accuracy_and_streak(cards):
    eng = QuizEngine(cards, QuizMode.SEQUENTIAL, trace_id="t-math")
    await eng.submit_answer("Paris")
    await eng.submit_answer("banana")
    r = await eng.submit_answer("Jupiter")
    s = r["score"]
    assert s.correct == 2 and s.accuracy == round(2 / 3, 4)
    assert eng.best_streak == 1  # streak reset by the miss


# ── hints ──────────────────────────────────────────────────────────────
async def test_hint_progression_no_answer_leak(cards):
    eng = QuizEngine(cards, QuizMode.ADAPTIVE, trace_id="t-hint", seed=1)
    h1 = await eng.hint()
    h2 = await eng.hint()
    assert h1["status"] == "ok" and h1["data"]["attempt"] == 1
    assert h1["data"]["hint_text"] != h2["data"]["hint_text"]
    assert eng.next_card.answer.lower() not in h1["data"]["hint_text"].lower()


# ── stats persistence ─────────────────────────────────────────────────
async def test_final_stats_written_to_disk(cards, monkeypatch, tmp_path):
    import tools.implementations.track_stats as ts

    monkeypatch.setattr(ts, "STATS_DIR", tmp_path)
    eng = QuizEngine(cards, QuizMode.SEQUENTIAL, trace_id="t-stats")
    final = await run_answers(eng, ["Paris", "four", "Jupiter"])
    assert final["finished"] and final["stats"]["saved_path"].endswith("t-stats.json")
    saved = (tmp_path / "t-stats.json").read_text()
    assert '"accuracy": 1.0' in saved


# ── LLM path + failure policy (online fake, no network) ───────────────
async def test_llm_verdict_used_when_available(cards, online_llm, monkeypatch):
    from llm import openai_client

    monkeypatch.setattr(
        openai_client, "chat_json",
        lambda messages, **kw: {"data": {"correct": False, "partial": True,
                                         "explanation": "Close but shallow.", "confidence": 0.8},
                                "usage": {"prompt_tokens": 20, "completion_tokens": 8}},
    )
    eng = QuizEngine(cards, QuizMode.SEQUENTIAL, trace_id="t-llm")
    r = await eng.submit_answer("near paris")
    assert r["verdict"].graded_by == "openai" and r["verdict"].partial


async def test_llm_failure_retries_then_falls_back(cards, online_llm, monkeypatch):
    from llm import openai_client

    calls = {"n": 0}

    def boom(messages, **kw):
        calls["n"] += 1
        raise openai_client.LLMUnavailableError("simulated outage")

    monkeypatch.setattr(openai_client, "chat_json", boom)
    eng = QuizEngine(cards, QuizMode.SEQUENTIAL, trace_id="t-llm-fail")
    r = await eng.submit_answer("Paris")
    assert r["verdict"].correct and r["verdict"].graded_by == "fallback-keyword"
    assert calls["n"] == 2  # grade_answer's retry policy: exactly one retry

"""Step 3 gate — Data Agent parses every supported layout and never crashes:
corrupted files yield friendly CardErrors with an empty card list, so the quiz
engine can never be reached with dirty data (design doc §5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.agents.data_agent import DataAgent
from agent.schemas import CardError, Flashcard

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
DECKS = Path(__file__).resolve().parent.parent / "data" / "sample_decks"


@pytest.fixture()
def agent() -> DataAgent:
    return DataAgent()


# ── happy paths ────────────────────────────────────────────────────────
async def test_valid_json_deck(agent):
    out = await agent.run(str(FIXTURES / "valid_deck.json"))
    assert out["errors"] == []
    assert len(out["cards"]) == 3
    assert all(isinstance(c, Flashcard) for c in out["cards"])
    assert out["cards"][0].question == "What is the capital of France?"


async def test_wrapped_json_and_sample_decks(agent):
    for deck in DECKS.iterdir():
        out = await agent.run(str(deck))
        assert out["cards"], f"{deck.name} produced no cards: {[e.reason for e in out['errors']]}"
        assert all(c.question and c.answer for c in out["cards"])


async def test_alternating_txt(agent):
    out = await agent.run(str(FIXTURES / "alternating_qa.txt"))
    # last question in fixture intentionally has no answer → 3 cards + 1 error
    assert len(out["cards"]) == 3
    assert len(out["errors"]) == 1
    assert "no answer" in out["errors"][0].reason.lower()


async def test_csv_and_bytes_stream(agent):
    data = (DECKS / "system_design.csv").read_bytes()
    out = await agent.run(data, file_format="csv")
    assert len(out["cards"]) == 5
    cap = next(c for c in out["cards"] if "CAP" in c.question)
    assert "Partition tolerance" in cap.answer  # quoted commas survive


# ── failure paths: friendly errors, zero cards, zero exceptions ────────
async def test_corrupted_json(agent):
    out = await agent.run(str(FIXTURES / "corrupted_syntax.json"))
    assert out["cards"] == []
    assert out["errors"]
    assert all(isinstance(e, CardError) for e in out["errors"])
    assert "not valid JSON" in out["errors"][0].reason


async def test_missing_fields_produces_partial_cards(agent):
    out = await agent.run(str(FIXTURES / "missing_fields.json"))
    assert len(out["cards"]) == 1  # only the complete card survives
    reasons = " ".join(e.reason for e in out["errors"])
    assert "missing a question" in reasons and "missing an answer" in reasons


async def test_nonexistent_path(agent):
    out = await agent.run("/definitely/not/here.json")
    assert out["cards"] == []
    assert "could not find the file" in out["errors"][0].reason


async def test_empty_file(agent, tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("[]")
    out = await agent.run(str(p))
    assert out["cards"] == []
    assert "No flashcards found" in out["errors"][0].reason


async def test_duplicate_questions_merge(agent, tmp_path):
    p = tmp_path / "dupes.json"
    p.write_text('[{"question": "Same?", "answer": "A"}, {"question": "same?", "answer": "B"}]')
    out = await agent.run(str(p))
    assert len(out["cards"]) == 1
    assert out["cards"][0].answer == "A"
    assert any("Duplicate" in e.reason for e in out["errors"])

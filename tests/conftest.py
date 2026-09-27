"""Shared test fixtures — pin the LLM to a known state so unit tests are
deterministic whether or not the developer's .env has a live key."""
from __future__ import annotations

import config as config_mod
import pytest

from agent.schemas import Flashcard

FAKE_VALID_KEY = "sk-" + "f4k3t3st" * 4  # well-formed, never used against network


def make_config(*, offline: bool) -> config_mod.Config:
    settings = config_mod.Settings(
        openai_api_key=("" if offline else FAKE_VALID_KEY),
        llm_validate_on_startup=False,
    )
    return config_mod.Config(settings=settings, llm_available=not offline, offline_mode=offline)


@pytest.fixture(autouse=True)
def offline_llm(monkeypatch):
    """Default: offline → grade_answer uses the deterministic keyword scorer."""
    monkeypatch.setattr(config_mod, "_cached", make_config(offline=True))
    monkeypatch.setattr("llm.openai_client._client", None, raising=True)
    yield


@pytest.fixture()
def online_llm(monkeypatch):
    """Opt-in: pretend LLM access; tests also fake chat_json itself."""
    monkeypatch.setattr(config_mod, "_cached", make_config(offline=False))


@pytest.fixture()
def cards() -> list[Flashcard]:
    return [
        Flashcard(id="c1", question="Capital of France?", answer="Paris", tags=["geo"]),
        Flashcard(id="c2", question="2 + 2?", answer="four", tags=["math"]),
        Flashcard(id="c3", question="Largest planet?", answer="Jupiter", tags=["astro"]),
    ]

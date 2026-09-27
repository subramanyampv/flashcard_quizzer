"""Step 2 gate tests — config.py static validation + startup gate semantics.

Live-tier tests fake the openai client so no real key/network is required.
"""
from __future__ import annotations

import types

import httpx
import openai
import pytest

from config import Config, ConfigError

VALID_KEY = "sk-" + "a1b2c3d4e5f6g7h8i9j0" * 2  # 42 chars, well-formed, not real


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_TEMPERATURE",
        "OPENAI_MAX_TOKENS",
        "OPENAI_BASE_URL",
        "LLM_VALIDATE_ON_STARTUP",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def make_env(tmp_path, **values):
    lines = [f"{k}={v}" for k, v in values.items()]
    path = tmp_path / ".env"
    path.write_text("\n".join(lines) + "\n")
    return path


# ── Tier 1: static validation ──────────────────────────────────────────
def test_missing_env_file_raises(tmp_path):
    with pytest.raises(ConfigError, match=r"cp \.env\.example"):
        Config.load(env_file=tmp_path / "nope.env")


def test_empty_key_with_validation_required_raises(tmp_path):
    env = make_env(tmp_path, OPENAI_API_KEY="", LLM_VALIDATE_ON_STARTUP="true")
    with pytest.raises(ConfigError, match="OPENAI_API_KEY is empty"):
        Config.load(env_file=env)


def test_malformed_key_raises_even_offline(tmp_path):
    env = make_env(tmp_path, OPENAI_API_KEY="not-a-real-key", LLM_VALIDATE_ON_STARTUP="false")
    with pytest.raises(ConfigError, match="does not look like a valid OpenAI key"):
        Config.load(env_file=env)


def test_temperature_out_of_bounds_raises(tmp_path):
    env = make_env(
        tmp_path, OPENAI_API_KEY=VALID_KEY, OPENAI_TEMPERATURE="3.5", LLM_VALIDATE_ON_STARTUP="false"
    )
    with pytest.raises(ConfigError, match="Invalid configuration"):
        Config.load(env_file=env)


def test_masked_key_never_exposes_full_secret(tmp_path):
    env = make_env(tmp_path, OPENAI_API_KEY=VALID_KEY, LLM_VALIDATE_ON_STARTUP="false")
    cfg = Config.load(env_file=env)
    assert VALID_KEY not in cfg.settings.masked_key
    assert cfg.settings.masked_key.endswith(VALID_KEY[-4:])


# ── Offline-mode semantics (the project's current operating mode) ─────
def test_no_key_offline_mode_degrades_without_error(tmp_path):
    env = make_env(tmp_path, LLM_VALIDATE_ON_STARTUP="false")
    cfg = Config.load(env_file=env)
    assert cfg.offline_mode and not cfg.llm_available
    report = cfg.run_startup_gate()
    assert not report.failed
    assert "OFFLINE" in report.render().upper()


def test_key_present_validation_off_skips_live_probe(tmp_path):
    env = make_env(tmp_path, OPENAI_API_KEY=VALID_KEY, LLM_VALIDATE_ON_STARTUP="false")
    cfg = Config.load(env_file=env)
    assert cfg.llm_available and not cfg.offline_mode  # usable, just not probed
    report = cfg.run_startup_gate()
    assert not report.failed
    assert "SKIPPED" in report.render()


# ── Tier 2: live probe (faked openai client, no network) ──────────────
def _auth_error():
    req = httpx.Request("GET", "https://api.openai.com/v1/models")
    return openai.AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None)


class _FakeModels:
    def __init__(self, exc=None):
        self._exc = exc

    def list(self):
        if self._exc:
            raise self._exc
        return []


class _FakeCompletions:
    def create(self, **kw):
        message = types.SimpleNamespace(content="pong")
        choice = types.SimpleNamespace(message=message)
        usage = types.SimpleNamespace(prompt_tokens=10, completion_tokens=1)
        return types.SimpleNamespace(choices=[choice], usage=usage)


def _install_fake_client(monkeypatch, models_exc=None):
    class FakeClient:
        def __init__(self, **kw):
            self.models = _FakeModels(models_exc)
            self.chat = type("C", (), {"completions": _FakeCompletions()})()

    monkeypatch.setattr(openai, "OpenAI", FakeClient)


def test_live_probe_success(tmp_path, monkeypatch):
    env = make_env(tmp_path, OPENAI_API_KEY=VALID_KEY, LLM_VALIDATE_ON_STARTUP="true")
    cfg = Config.load(env_file=env)
    _install_fake_client(monkeypatch)
    report = cfg.validate_llm_access()
    assert not report.failed
    assert any("OPERATIONAL" in c.detail for c in report.checks)


def test_live_probe_auth_failure_maps_remediation(tmp_path, monkeypatch):
    env = make_env(tmp_path, OPENAI_API_KEY=VALID_KEY, LLM_VALIDATE_ON_STARTUP="true")
    cfg = Config.load(env_file=env)
    _install_fake_client(monkeypatch, models_exc=_auth_error())
    with pytest.raises(ConfigError, match="REJECTED"):
        cfg.validate_llm_access()


def test_llm_client_refuses_offline_mode(tmp_path, monkeypatch):
    env = make_env(tmp_path, LLM_VALIDATE_ON_STARTUP="false")
    offline_cfg = Config.load(env_file=env)
    import llm.openai_client as client_mod

    monkeypatch.setattr(client_mod, "get_config", lambda: offline_cfg)
    with pytest.raises(client_mod.LLMUnavailableError):
        client_mod.get_client()

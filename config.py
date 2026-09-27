"""Typed configuration + 3-tier OpenAI validation engine (PLAN.md §5).

Tier 1 — static:  .env exists, key format/length, numeric bounds. Always runs.
Tier 2 — live:    client.models.list() + a 5-token ping on OPENAI_MODEL.
                  Skipped (with a WARN) when LLM_VALIDATE_ON_STARTUP=false,
                  in which case LLM tools degrade to deterministic fallbacks.
Tier 3 — gate:    run_startup_gate() — the first call every entrypoint makes.

Usage:
    from config import Config, ConfigError
    cfg = Config.load()               # tier 1 (raises ConfigError with a fix hint)
    cfg.validate_llm_access()         # tier 2 (raises ConfigError with remediation)
    cfg.run_startup_gate()            # tier 3: both tiers + health report

    python config.py --check          # standalone diagnostic table, exit 0/1
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"
LOGS_DIR = PROJECT_ROOT / "logs"

# Valid OpenAI keys: classic `sk-...`, project `sk-proj-...`, service `sk-svc-...`
_KEY_RE = re.compile(r"^sk-[A-Za-z0-9_\-]{20,}$")


class ConfigError(RuntimeError):
    """Raised with a human-readable remediation message — never a raw traceback."""


class Settings(BaseSettings):
    """Everything that comes from .env / environment. Single source of truth."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE), env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_temperature: float = Field(0.3, ge=0.0, le=2.0)
    openai_max_tokens: int = Field(400, ge=1, le=4096)
    openai_base_url: str = ""
    llm_validate_on_startup: bool = True
    log_level: str = "INFO"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Blueprint §4 safety settings — all on by default
    safety_block_pii: bool = True
    safety_block_prompt_injection: bool = True
    safety_check_output_toxicity: bool = True

    @property
    def masked_key(self) -> str:
        """sk-…{last4} — the only form the key may ever appear in logs/UI."""
        if not self.openai_api_key:
            return "(not set)"
        return f"sk-…{self.openai_api_key[-4:]}"


@dataclass
class CheckResult:
    name: str
    status: str  # PASS | WARN | FAIL
    detail: str

    @property
    def mark(self) -> str:
        return {"PASS": "✓", "WARN": "!", "FAIL": "✗"}[self.status]


@dataclass
class HealthReport:
    checks: List[CheckResult] = field(default_factory=list)
    offline: bool = False  # LLM tools are degraded to fallbacks (distinct from a mere WARN)

    @property
    def failed(self) -> bool:
        return any(c.status == "FAIL" for c in self.checks)

    def render(self) -> str:
        width = 60
        lines = ["=" * width, "OPENAI & CONFIGURATION HEALTH CHECK".center(width), "=" * width]
        for c in self.checks:
            lines.append(f"[{c.mark}] {c.name:<40}: {c.detail}")
        lines.append("-" * width)
        if self.failed:
            lines.append("Status: CHECKS FAILED — fix the items above, then re-run.")
        elif self.offline:
            lines.append("Status: RUNNING IN OFFLINE MODE — LLM tools use fallbacks.")
        elif any(c.status == "WARN" for c in self.checks):
            lines.append("Status: LIVE LLM READY — optional integrations missing (see ! lines).")
        else:
            lines.append("Status: ALL CHECKS PASSED — Ready to run.")
        lines.append("=" * width)
        return "\n".join(lines)


@dataclass
class Config:
    """Typed, validated view of the environment used by every module."""

    settings: Settings
    llm_available: bool  # True once tier-2 passed, or tier-2 skipped but key looks valid
    offline_mode: bool   # True when LLM calls must fall back to deterministic logic

    # ── Tier 1: static validation ─────────────────────────────────────
    @classmethod
    def load(cls, env_file: Optional[Path] = None) -> "Config":
        env_path = Path(env_file) if env_file else ENV_FILE
        if not env_path.exists() and not os.environ.get("OPENAI_API_KEY"):
            raise ConfigError(
                f"No .env found at {env_path}. Fix: cp .env.example .env and set OPENAI_API_KEY "
                "(or export OPENAI_API_KEY in your shell)."
            )
        try:
            settings = Settings(_env_file=str(env_path) if env_path.exists() else None)
        except ValidationError as exc:
            bad = "; ".join(" → ".join(str(p) for p in e["loc"]) + f": {e['msg']}" for e in exc.errors())
            raise ConfigError(
                f"Invalid configuration in .env — {bad}. Fix the values in {env_path} and retry."
            ) from exc

        key = settings.openai_api_key.strip()
        live_check = settings.llm_validate_on_startup

        if not key:
            if live_check:
                raise ConfigError(
                    "OPENAI_API_KEY is empty but LLM_VALIDATE_ON_STARTUP=true. Add your key to "
                    f"{env_path}, or set LLM_VALIDATE_ON_STARTUP=false to run in offline mode "
                    "(deterministic fallbacks instead of live grading)."
                )
            return cls(settings=settings, llm_available=False, offline_mode=True)

        if not _KEY_RE.match(key):
            raise ConfigError(
                f"OPENAI_API_KEY ({settings.masked_key}) does not look like a valid OpenAI key "
                "(expected sk-… at least 20 chars after the prefix). Re-copy the key in "
                f"{env_path} — watch for missing characters, quotes or surrounding spaces."
            )
        # Key present + well-formed: LLM tools may use it (the validate flag only
        # controls whether the live PROBE runs at startup). Calls still degrade
        # gracefully to fallbacks if a request fails at runtime.
        return cls(settings=settings, llm_available=True, offline_mode=False)

    # ── Tier 2: live access validation ────────────────────────────────
    def validate_llm_access(self, report: Optional[HealthReport] = None) -> HealthReport:
        """Prove auth + connectivity + model access. Raises ConfigError on failure."""
        report = report if report is not None else HealthReport()
        if not self.settings.llm_validate_on_startup:
            report.checks.append(
                CheckResult("Live LLM Access Probe", "WARN", "SKIPPED (LLM_VALIDATE_ON_STARTUP=false)")
            )
            return report

        try:
            from openai import APIConnectionError, APIStatusError, AuthenticationError, OpenAI, RateLimitError

            client = OpenAI(
                api_key=self.settings.openai_api_key,
                base_url=self.settings.openai_base_url or None,
                timeout=20.0,
                max_retries=0,  # the probe itself must not retry
            )
            try:
                client.models.list()
                auth_detail = "AUTHENTICATED"
            except AuthenticationError:
                raise ConfigError(
                    f"OPENAI_API_KEY ({self.settings.masked_key}) was REJECTED by OpenAI. "
                    "Check the key in .env (platform.openai.com/api-keys)."
                )
            except RateLimitError:
                raise ConfigError("Key is valid but rate-limited — wait a minute and retry.")
            except APIConnectionError:
                raise ConfigError(
                    "No network route to api.openai.com — check VPN/firewall/proxy, or set "
                    "OPENAI_BASE_URL for a compatible endpoint."
                )

            # Deeper probe: models.list() proves the key, not that this key may
            # chat on the configured model. Spend ~5 tokens to prove both.
            t0 = time.perf_counter()
            try:
                probe = client.chat.completions.create(
                    model=self.settings.openai_model,
                    messages=[{"role": "user", "content": "Reply with the single word: pong"}],
                    max_tokens=5,
                )
                _ = probe.choices[0].message  # noqa: F841 — touch to prove well-formed response
            except APIStatusError as exc:
                if exc.status_code in (400, 404):
                    raise ConfigError(
                        f"Model '{self.settings.openai_model}' is not accessible with this key "
                        f"(HTTP {exc.status_code}). Set OPENAI_MODEL to a model your plan allows."
                    ) from exc
                raise ConfigError(f"OpenAI probe failed: HTTP {exc.status_code} — {exc}") from exc
            latency_ms = (time.perf_counter() - t0) * 1000

            self.llm_available = True
            self.offline_mode = False
            report.checks.append(CheckResult("OpenAI API Connectivity", "PASS", auth_detail))
            report.checks.append(
                CheckResult(
                    f"Model '{self.settings.openai_model}' Access",
                    "PASS",
                    f"OPERATIONAL (Latency: {latency_ms:.0f}ms)",
                )
            )
        except ImportError as exc:  # pragma: no cover
            raise ConfigError(f"openai package unavailable in this environment: {exc}") from exc
        return report

    # ── Tier 3: startup gate (called first by every entrypoint) ───────
    def run_startup_gate(self) -> HealthReport:
        """Static already passed at load(); add live probe + observability line.

        Raises ConfigError with remediation text if anything hard-fails.
        """
        report = HealthReport()
        report.checks.append(
            CheckResult("Configuration File (.env)", "PASS", str(ENV_FILE) if ENV_FILE.exists() else "env vars only")
        )
        report.checks.append(
            CheckResult(
                "OpenAI API Key Format",
                "PASS" if self.settings.openai_api_key else ("WARN" if self.offline_mode else "FAIL"),
                self.settings.masked_key + ("" if self.settings.openai_api_key else " — offline fallbacks active"),
            )
        )
        self.validate_llm_access(report)
        report.offline = self.offline_mode or not self.settings.openai_api_key
        using_langfuse = bool(self.settings.langfuse_public_key and self.settings.langfuse_secret_key)
        report.checks.append(
            CheckResult(
                "Observability Backend",
                "PASS" if using_langfuse else "WARN",
                "LANGFUSE" if using_langfuse else "LOCAL FALLBACK (logs/interactions.log)",
            )
        )
        if report.failed:
            raise ConfigError("Startup gate failed:\n" + report.render())
        return report

    # Convenience passthroughs so modules import config only (blueprint rule 8)
    @property
    def model(self) -> str:
        return self.settings.openai_model

    @property
    def temperature(self) -> float:
        return self.settings.openai_temperature

    @property
    def max_tokens(self) -> int:
        return self.settings.openai_max_tokens

    @property
    def log_level(self) -> str:
        return self.settings.log_level


# ── cached singleton for non-CLI imports ───────────────────────────────
_cached: Optional[Config] = None


def get_config() -> Config:
    """Load once, reuse everywhere. Raises ConfigError on invalid static config."""
    global _cached
    if _cached is None:
        _cached = Config.load()
    return _cached


def reset_config_cache() -> None:
    """Test hook: drop the cached Config so env changes take effect."""
    global _cached
    _cached = None


def run_health_check() -> int:
    """Entry point for `python config.py --check`. Returns shell exit code."""
    try:
        cfg = Config.load()
        report = cfg.run_startup_gate()
    except ConfigError as exc:
        print(f"\nCONFIG ERROR: {exc}\n")
        return 1
    print()
    print(report.render())
    print()
    return 1 if report.failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Configuration & OpenAI access health check")
    parser.add_argument("--check", action="store_true", help="run all validation tiers and print a report")
    args = parser.parse_args()
    sys.exit(run_health_check() if args.check else 0)

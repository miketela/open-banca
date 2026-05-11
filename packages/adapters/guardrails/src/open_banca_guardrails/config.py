"""Guardrail configuration via environment variables.

Environment variables (prefix: GUARDRAIL_):
    GUARDRAIL_DB_PATH            — Path to the guardrails SQLite DB.
    GUARDRAIL_LLM_JOB_CAP_USD   — Max LLM cost per job (default $0.50).
    GUARDRAIL_SCRAPE_CAP_USD     — Max cost per scrape (default $0.10).
    GUARDRAIL_MAX_TOKENS_PER_JOB — Max tokens per agent per job (default 100000).
    GUARDRAIL_REMAP_LIMIT_24H    — Max remap attempts per bank per 24h (default 3).
    GUARDRAIL_MAPPING_LIMIT_24H  — Max mapping runs per bank per 24h (default 1).
    GUARDRAIL_LOGIN_FAIL_LIMIT   — Consecutive login failures before open (default 2).
    GUARDRAIL_CB_COOLDOWN_HOURS  — Circuit breaker cooldown in hours (default 1).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class GuardrailSettings(BaseSettings):
    """Runtime configuration for the guardrails adapter."""

    model_config = SettingsConfigDict(
        env_prefix="GUARDRAIL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    db_path: Path = Path("./guardrails.db")

    # Budget caps
    llm_job_cap_usd: Decimal = Decimal("0.50")
    scrape_cap_usd: Decimal = Decimal("0.10")
    max_tokens_per_job: int = 100_000

    # Rate limits
    remap_limit_24h: int = 3
    mapping_limit_24h: int = 1
    mapping_limit_override: bool = False  # set True to bypass mapping_limit_24h

    # Circuit breaker
    login_fail_limit: int = 2
    cb_cooldown_hours: int = 1


def get_settings() -> GuardrailSettings:
    """Return a fresh settings instance (reads env at call time)."""
    return GuardrailSettings()

"""Tests for open_banca_llm.router — unified LLM model resolution and cost helpers."""

from __future__ import annotations

import logging
from decimal import Decimal
from unittest.mock import patch

import pytest

from open_banca_llm.router import (
    LLMConfigurationError,
    compute_cost,
    get_cost_cap,
    resolve_model,
)


class TestResolveModel:
    """resolve_model() tests."""

    def test_env_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit OPEN_BANCA_<AGENT>_MODEL env var takes precedence."""
        monkeypatch.setenv("OPEN_BANCA_MAPPER_MODEL", "openai/gpt-4o")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        assert resolve_model("mapper") == "openai/gpt-4o"

    def test_env_override_validator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPEN_BANCA_VALIDATOR_MODEL", "mistral/mistral-large")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        assert resolve_model("validator") == "mistral/mistral-large"

    def test_default_mapper_uses_claude(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without override, mapper defaults to Claude Sonnet 4.6."""
        monkeypatch.delenv("OPEN_BANCA_MAPPER_MODEL", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        assert resolve_model("mapper") == "anthropic/claude-sonnet-4-6"

    def test_default_remapper_uses_claude(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPEN_BANCA_REMAPPER_MODEL", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        assert resolve_model("remapper") == "anthropic/claude-sonnet-4-6"

    def test_default_validator_prefers_deepseek(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Validator defaults to DeepSeek when DEEPSEEK_API_KEY is present."""
        monkeypatch.delenv("OPEN_BANCA_VALIDATOR_MODEL", raising=False)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        assert resolve_model("validator") == "deepseek/deepseek-chat"

    def test_default_judge_prefers_deepseek(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPEN_BANCA_JUDGE_MODEL", raising=False)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        assert resolve_model("judge") == "deepseek/deepseek-chat"

    def test_validator_falls_to_haiku_without_deepseek(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without DEEPSEEK_API_KEY, validator falls back to Claude Haiku."""
        monkeypatch.delenv("OPEN_BANCA_VALIDATOR_MODEL", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        assert resolve_model("validator") == "anthropic/claude-haiku-4-5"

    def test_single_provider_fallback_anthropic_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With only ANTHROPIC_API_KEY, all agents use Claude models."""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        for agent in ("mapper", "remapper", "validator", "judge"):
            monkeypatch.delenv(f"OPEN_BANCA_{agent.upper()}_MODEL", raising=False)

        assert "anthropic/" in resolve_model("mapper")
        assert "anthropic/" in resolve_model("remapper")
        assert "anthropic/" in resolve_model("validator")
        assert "anthropic/" in resolve_model("judge")

    def test_no_keys_raises_configuration_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No LLM keys at all raises LLMConfigurationError."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        for agent in ("mapper", "remapper", "validator", "judge"):
            monkeypatch.delenv(f"OPEN_BANCA_{agent.upper()}_MODEL", raising=False)

        with pytest.raises(LLMConfigurationError):
            resolve_model("mapper")


class TestComputeCost:
    """compute_cost() tests."""

    def test_known_model_returns_positive(self) -> None:
        """compute_cost with a known model should return > 0."""
        cost = compute_cost("anthropic/claude-sonnet-4-6", input_tokens=1000, output_tokens=500)
        assert cost > Decimal("0")

    def test_unknown_model_returns_zero_with_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """compute_cost with unknown model returns 0 and logs warning."""
        with caplog.at_level(logging.WARNING, logger="open_banca_llm.router"):
            cost = compute_cost("unknown/fake-model-xyz", input_tokens=1000, output_tokens=500)
        assert cost == Decimal("0")
        assert any("unknown/fake-model-xyz" in r.message for r in caplog.records)

    def test_zero_tokens_returns_zero(self) -> None:
        cost = compute_cost("anthropic/claude-sonnet-4-6", input_tokens=0, output_tokens=0)
        assert cost == Decimal("0")


class TestGetCostCap:
    """get_cost_cap() tests."""

    def test_validator_deepseek_cap(self) -> None:
        cap = get_cost_cap("validator", "deepseek/deepseek-chat")
        assert cap == Decimal("0.05")

    def test_validator_haiku_cap(self) -> None:
        cap = get_cost_cap("validator", "anthropic/claude-haiku-4-5")
        assert cap == Decimal("0.10")

    def test_validator_sonnet_cap(self) -> None:
        cap = get_cost_cap("validator", "anthropic/claude-sonnet-4-6")
        assert cap == Decimal("0.15")

    def test_judge_deepseek_cap(self) -> None:
        cap = get_cost_cap("judge", "deepseek/deepseek-chat")
        assert cap == Decimal("0.02")

    def test_judge_haiku_cap(self) -> None:
        cap = get_cost_cap("judge", "anthropic/claude-haiku-4-5")
        assert cap == Decimal("0.05")

    def test_judge_sonnet_cap(self) -> None:
        cap = get_cost_cap("judge", "anthropic/claude-sonnet-4-6")
        assert cap == Decimal("0.10")

    def test_judge_opus_cap(self) -> None:
        cap = get_cost_cap("judge", "anthropic/claude-opus-4-7")
        assert cap == Decimal("0.20")

    def test_validator_opus_cap(self) -> None:
        cap = get_cost_cap("validator", "anthropic/claude-opus-4-7")
        assert cap == Decimal("0.30")

    def test_mapper_returns_global_cap(self) -> None:
        """Mapper/Remapper use the global $0.50/job cap."""
        cap = get_cost_cap("mapper", "anthropic/claude-sonnet-4-6")
        assert cap == Decimal("0.50")

    def test_remapper_returns_global_cap(self) -> None:
        cap = get_cost_cap("remapper", "anthropic/claude-sonnet-4-6")
        assert cap == Decimal("0.50")

    def test_unknown_model_falls_to_default(self) -> None:
        """Unknown model for validator/judge gets a sane default cap."""
        cap = get_cost_cap("validator", "openai/gpt-4o")
        assert cap > Decimal("0")

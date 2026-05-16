"""Unified LLM model router — resolve model per agent, compute costs, enforce caps.

Central resolution point so all agents use a consistent model selection strategy:
  1. Explicit env override (OPEN_BANCA_<AGENT>_MODEL)
  2. PRD defaults per agent role
  3. Single-provider fallback when only one API key is available

See docs/01-architecture/multi-agent.md and CLAUDE.md §Tech Stack.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Literal

logger = logging.getLogger(__name__)

AgentRole = Literal["mapper", "remapper", "validator", "judge"]

_ENV_KEYS: dict[AgentRole, str] = {
    "mapper": "OPEN_BANCA_MAPPER_MODEL",
    "remapper": "OPEN_BANCA_REMAPPER_MODEL",
    "validator": "OPEN_BANCA_VALIDATOR_MODEL",
    "judge": "OPEN_BANCA_JUDGE_MODEL",
}

_PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
}


class LLMConfigurationError(Exception):
    """No LLM provider keys available."""


def _has_key(provider: str) -> bool:
    env_var = _PROVIDER_KEY_ENV.get(provider, "")
    val = os.environ.get(env_var, "")
    return bool(val and val.strip())


def _provider_of(model: str) -> str:
    """Extract provider prefix from a LiteLLM model string."""
    return model.split("/", 1)[0] if "/" in model else model


def _any_key_available() -> str | None:
    """Return the first available provider name, or None."""
    for provider in ("anthropic", "deepseek", "openai"):
        if _has_key(provider):
            return provider
    return None


def resolve_model(agent: AgentRole) -> str:
    """Resolve the LLM model string for a given agent role.

    Priority:
      1. OPEN_BANCA_<AGENT>_MODEL env var (explicit override)
      2. PRD defaults: mapper/remapper=Claude Sonnet, validator/judge=DeepSeek (if key) else Haiku
      3. Single-provider fallback

    Raises:
        LLMConfigurationError: When no LLM API keys are available.
    """
    env_var = _ENV_KEYS[agent]
    override = os.environ.get(env_var, "").strip()

    if override:
        logger.info("resolve_model agent=%s model=%s reason=env_override", agent, override)
        return override

    model = _default_for_agent(agent)

    provider = _provider_of(model)
    if _has_key(provider):
        logger.info("resolve_model agent=%s model=%s reason=default", agent, model)
        return model

    fallback_provider = _any_key_available()
    if fallback_provider is None:
        raise LLMConfigurationError(
            f"No LLM API keys available for agent '{agent}'. "
            f"Set at least one of: ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, OPENAI_API_KEY"
        )

    model = _fallback_model(agent, fallback_provider)
    logger.info(
        "resolve_model agent=%s model=%s reason=single_provider_fallback provider=%s",
        agent, model, fallback_provider,
    )
    return model


def _default_for_agent(agent: AgentRole) -> str:
    """Return the PRD-specified default model for each agent role."""
    if agent in ("mapper", "remapper"):
        return "anthropic/claude-sonnet-4-6"

    if _has_key("deepseek"):
        return "deepseek/deepseek-chat"
    if _has_key("anthropic"):
        return "anthropic/claude-haiku-4-5"

    return "anthropic/claude-haiku-4-5"


def _fallback_model(agent: AgentRole, provider: str) -> str:
    """Return a fallback model for a given agent when the default provider is absent."""
    _provider_models: dict[str, dict[AgentRole, str]] = {
        "anthropic": {
            "mapper": "anthropic/claude-sonnet-4-6",
            "remapper": "anthropic/claude-sonnet-4-6",
            "validator": "anthropic/claude-haiku-4-5",
            "judge": "anthropic/claude-haiku-4-5",
        },
        "deepseek": {
            "mapper": "deepseek/deepseek-chat",
            "remapper": "deepseek/deepseek-chat",
            "validator": "deepseek/deepseek-chat",
            "judge": "deepseek/deepseek-chat",
        },
        "openai": {
            "mapper": "openai/gpt-4o",
            "remapper": "openai/gpt-4o",
            "validator": "openai/gpt-4o-mini",
            "judge": "openai/gpt-4o-mini",
        },
    }
    return _provider_models.get(provider, {}).get(agent, f"{provider}/unknown")


def compute_cost(model: str, *, input_tokens: int, output_tokens: int) -> Decimal:
    """Compute USD cost for a model call using LiteLLM's pricing table.

    Falls back to Decimal("0") with a warning if LiteLLM doesn't recognize the model.
    """
    if input_tokens == 0 and output_tokens == 0:
        return Decimal("0")

    try:
        import litellm  # type: ignore[import-untyped]

        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
        )
        return Decimal(str(prompt_cost)) + Decimal(str(completion_cost))
    except Exception:
        logger.warning(
            "compute_cost: LiteLLM could not price model=%s — returning $0.00", model
        )
        return Decimal("0")


_COST_CAPS: dict[AgentRole, dict[str, Decimal]] = {
    "validator": {
        "deepseek": Decimal("0.05"),
        "haiku": Decimal("0.10"),
        "sonnet": Decimal("0.15"),
        "opus": Decimal("0.30"),
        "_default": Decimal("0.15"),
    },
    "judge": {
        "deepseek": Decimal("0.02"),
        "haiku": Decimal("0.05"),
        "sonnet": Decimal("0.10"),
        "opus": Decimal("0.20"),
        "_default": Decimal("0.10"),
    },
}

_GLOBAL_JOB_CAP = Decimal("0.50")


def to_pydantic_ai_model_str(litellm_model: str) -> str:
    """Convert LiteLLM model string (provider/model) to PydanticAI format (provider:model)."""
    if "/" in litellm_model:
        return litellm_model.replace("/", ":", 1)
    return litellm_model


def get_cost_cap(agent: AgentRole, model: str) -> Decimal:
    """Return the per-call cost cap for a given agent and model.

    Mapper/Remapper use the global $0.50/job cap.
    Validator/Judge have model-specific caps.
    """
    if agent in ("mapper", "remapper"):
        return _GLOBAL_JOB_CAP

    caps = _COST_CAPS.get(agent, {})
    model_lower = model.lower()

    if "deepseek" in model_lower:
        return caps.get("deepseek", caps.get("_default", _GLOBAL_JOB_CAP))
    if "haiku" in model_lower:
        return caps.get("haiku", caps.get("_default", _GLOBAL_JOB_CAP))
    if "opus" in model_lower:
        return caps.get("opus", caps.get("_default", _GLOBAL_JOB_CAP))
    if "sonnet" in model_lower:
        return caps.get("sonnet", caps.get("_default", _GLOBAL_JOB_CAP))

    return caps.get("_default", _GLOBAL_JOB_CAP)

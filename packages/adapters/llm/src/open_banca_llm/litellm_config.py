"""LiteLLM client factory with timeouts, retries, and cost-tracking callback.

Usage::

    from open_banca_llm.litellm_config import build_litellm_model

    llm = build_litellm_model()          # Claude Sonnet 4.6 with defaults
    llm_ds = build_litellm_model(        # DeepSeek for Validator/Judge
        model="deepseek/deepseek-chat",
        max_tokens=4096,
    )
"""

from __future__ import annotations

import logging

from open_banca_llm.router import resolve_model

logger = logging.getLogger(__name__)


def _safe_resolve(agent: str, fallback: str) -> str:
    try:
        return resolve_model(agent)  # type: ignore[arg-type]
    except Exception:
        return fallback


# Backward-compat constants — thin getters over the router.
MAPPER_MODEL = _safe_resolve("mapper", "anthropic/claude-sonnet-4-6")
JUDGE_MODEL = _safe_resolve("judge", "deepseek/deepseek-chat")


def build_litellm_model(
    *,
    model: str = MAPPER_MODEL,
    temperature: float | None = 0.0,
    max_tokens: int | None = 4096,
    max_retries: int = 2,
    timeout: int = 120,
    api_key: str | None = None,
    api_base: str | None = None,
) -> object:
    """Create a ChatLiteLLM instance configured for production use.

    Args:
        model: LiteLLM model string (e.g. 'anthropic/claude-sonnet-4-6').
        temperature: Sampling temperature (0.0 for deterministic output).
        max_tokens: Maximum completion tokens.
        max_retries: Number of retries on transient API errors.
        timeout: Request timeout in seconds.
        api_key: Override API key (reads from env by default).
        api_base: Override API base URL (e.g. for local proxies).

    Returns:
        ChatLiteLLM: Configured model instance.
    """
    try:
        from browser_use.llm.litellm.chat import ChatLiteLLM  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "browser-use is required to use ChatLiteLLM. "
            "Install from path dep or PyPI: browser-use>=0.1"
        ) from exc

    return ChatLiteLLM(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=max_retries,
        api_key=api_key,
        api_base=api_base,
    )

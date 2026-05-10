"""PydanticAI-compatible setup stub.

Note: The Mapper agent uses browser-use + LiteLLM directly (not PydanticAI
model framework) because browser-use's Agent loop requires its own BaseChatModel
Protocol, which is incompatible with PydanticAI's model interface.

This module provides:
  - ``FakeChatModel`` re-exported for test usage convenience.
  - A factory helper for setting up the Mapper LLM stack.

The 'PydanticAI test models' referenced in CLAUDE.md are fulfilled by
``FakeChatModel`` (which implements BaseChatModel Protocol) — zero real API calls.
"""

from __future__ import annotations

from open_banca_llm.litellm_config import MAPPER_MODEL, build_litellm_model
from open_banca_llm.mapper.agent import FakeChatModel
from open_banca_llm.mapper.cost_tracker import CostTracker
from open_banca_llm.mapper.pii_filter_adapter import PIIRedactingChatModel

__all__ = [
    "MAPPER_MODEL",
    "CostTracker",
    "FakeChatModel",
    "PIIRedactingChatModel",
    "build_litellm_model",
]

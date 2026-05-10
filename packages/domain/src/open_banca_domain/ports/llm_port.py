"""LLMPort — structured LLM completions with token tracking."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    model: str


@runtime_checkable
class LLMPort(Protocol):
    """Provider-agnostic LLM interface with optional vision and tool use."""

    def invoke(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        sensitive_data: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...

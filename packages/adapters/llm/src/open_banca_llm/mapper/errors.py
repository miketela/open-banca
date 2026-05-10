"""Mapper agent error hierarchy."""

from __future__ import annotations


class MapperError(Exception):
    """Base error for Mapper agent failures."""


class CostExceeded(MapperError):
    """Raised when cumulative LLM cost exceeds the configured budget cap."""

    def __init__(self, cost_usd: float, cap_usd: float) -> None:
        self.cost_usd = cost_usd
        self.cap_usd = cap_usd
        super().__init__(f"LLM cost ${cost_usd:.4f} exceeded cap ${cap_usd:.2f}")


class WallclockExceeded(MapperError):
    """Raised when the mapper run exceeds the wallclock timeout."""


class SelfTestFailed(MapperError):
    """Raised when the generated BankMap fails the dry-run self-test."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"BankMap self-test failed: {reason}")

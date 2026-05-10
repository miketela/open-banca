"""Cost and wallclock tracker for the Mapper agent.

Accumulates LLM token usage and estimated cost across browser-use steps.
Raises CostExceeded when the cap is hit and WallclockExceeded when the
wallclock limit is reached.

ADR-0020 / REQ-011: $0.50 hard cap, 5 min wallclock.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock

from open_banca_llm.mapper.errors import CostExceeded, WallclockExceeded

# Default pricing fallback (USD per 1K tokens).
# Actual pricing comes from litellm.completion_cost() when available.
_DEFAULT_INPUT_COST_PER_1K = 0.003  # claude-sonnet-4 input
_DEFAULT_OUTPUT_COST_PER_1K = 0.015  # claude-sonnet-4 output

DEFAULT_COST_CAP_USD: float = 0.50
DEFAULT_WALLCLOCK_SECONDS: float = 300.0  # 5 min


@dataclass
class TokenUsage:
    """Accumulated token counts for a mapper run."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class CostTracker:
    """Thread-safe accumulator of LLM token cost during a mapper run.

    Args:
        cost_cap_usd: Abort threshold in USD (default $0.50).
        wallclock_cap_seconds: Abort threshold in wall-clock seconds (default 300).
    """

    def __init__(
        self,
        cost_cap_usd: float = DEFAULT_COST_CAP_USD,
        wallclock_cap_seconds: float = DEFAULT_WALLCLOCK_SECONDS,
    ) -> None:
        self._cap = cost_cap_usd
        self._wallclock_cap = wallclock_cap_seconds
        self._usage = TokenUsage()
        self._lock = Lock()
        self._started_at: float = time.monotonic()

    def record(
        self,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None = None,
    ) -> None:
        """Record one LLM call's usage and check caps.

        Args:
            input_tokens: Prompt tokens consumed.
            output_tokens: Completion tokens consumed.
            cost_usd: Pre-computed cost from litellm (preferred). If None,
                      falls back to hardcoded pricing table.

        Raises:
            CostExceeded: Immediately if cost cap is crossed.
            WallclockExceeded: Immediately if wallclock cap is crossed.
        """
        if cost_usd is None:
            cost_usd = (
                input_tokens / 1000 * _DEFAULT_INPUT_COST_PER_1K
                + output_tokens / 1000 * _DEFAULT_OUTPUT_COST_PER_1K
            )

        with self._lock:
            self._usage.input_tokens += input_tokens
            self._usage.output_tokens += output_tokens
            self._usage.cost_usd += cost_usd
            running_cost = self._usage.cost_usd

        self._check_wallclock()

        if running_cost > self._cap:
            raise CostExceeded(running_cost, self._cap)

    def _check_wallclock(self) -> None:
        elapsed = time.monotonic() - self._started_at
        if elapsed > self._wallclock_cap:
            raise WallclockExceeded(
                f"Mapper run exceeded {self._wallclock_cap}s wallclock limit "
                f"(elapsed {elapsed:.1f}s)"
            )

    @property
    def usage(self) -> TokenUsage:
        with self._lock:
            return TokenUsage(
                input_tokens=self._usage.input_tokens,
                output_tokens=self._usage.output_tokens,
                cost_usd=self._usage.cost_usd,
            )

    async def should_stop_async(self) -> bool:
        """Async should-stop callback for browser-use ``register_should_stop_callback``.

        browser-use requires ``Callable[[], Awaitable[bool]]`` for this hook.
        Returns True if cost or wallclock caps are already exceeded.
        """
        return self.should_stop()

    def should_stop(self) -> bool:
        """Return True if cost or wallclock caps are already exceeded (sync).

        Use ``should_stop_async`` for the browser-use agent callback.
        """
        try:
            self._check_wallclock()
        except WallclockExceeded:
            return True

        with self._lock:
            return self._usage.cost_usd > self._cap

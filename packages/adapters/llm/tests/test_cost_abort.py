"""Test: CostTracker raises CostExceeded when accumulated cost > $0.50.

Also tests: WallclockExceeded when wallclock cap is hit.
REQ-011: $0.50 LLM cost hard cap per job.
"""

from __future__ import annotations

import time

import pytest

from open_banca_llm.mapper.cost_tracker import CostTracker
from open_banca_llm.mapper.errors import CostExceeded, WallclockExceeded


def test_cost_within_cap_does_not_raise() -> None:
    """Small cost increments below cap never raise."""
    tracker = CostTracker(cost_cap_usd=0.50)
    tracker.record(input_tokens=100, output_tokens=50, cost_usd=0.001)
    tracker.record(input_tokens=100, output_tokens=50, cost_usd=0.001)
    # No exception — test passes


def test_cost_exactly_at_cap_does_not_raise() -> None:
    """Exactly at the cap (==) does not raise — only > cap raises."""
    tracker = CostTracker(cost_cap_usd=0.50)
    tracker.record(input_tokens=0, output_tokens=0, cost_usd=0.50)
    # No exception — cumulative cost == cap is allowed


def test_cost_exceeds_cap_raises_cost_exceeded() -> None:
    """Cost > cap raises CostExceeded with correct values."""
    tracker = CostTracker(cost_cap_usd=0.50)
    with pytest.raises(CostExceeded) as exc_info:
        tracker.record(input_tokens=1000, output_tokens=500, cost_usd=0.51)

    exc = exc_info.value
    assert exc.cap_usd == 0.50
    assert exc.cost_usd > 0.50


def test_cost_accumulates_across_calls_and_raises() -> None:
    """Multiple small calls accumulate; raises when total crosses cap."""
    tracker = CostTracker(cost_cap_usd=0.10)
    tracker.record(input_tokens=100, output_tokens=50, cost_usd=0.04)
    tracker.record(input_tokens=100, output_tokens=50, cost_usd=0.04)

    with pytest.raises(CostExceeded):
        tracker.record(input_tokens=100, output_tokens=50, cost_usd=0.03)  # total = 0.11


def test_cost_exceeded_message_contains_values() -> None:
    """CostExceeded.__str__ includes both actual cost and cap."""
    exc = CostExceeded(cost_usd=0.75, cap_usd=0.50)
    msg = str(exc)
    assert "0.50" in msg or "0.7" in msg


def test_usage_property_reflects_accumulated_tokens() -> None:
    """usage property returns cumulative token counts after recording."""
    tracker = CostTracker(cost_cap_usd=1.0)
    tracker.record(input_tokens=100, output_tokens=50, cost_usd=0.001)
    tracker.record(input_tokens=200, output_tokens=100, cost_usd=0.002)
    usage = tracker.usage
    assert usage.input_tokens == 300
    assert usage.output_tokens == 150
    assert abs(usage.cost_usd - 0.003) < 1e-9


def test_should_stop_returns_true_after_cost_exceeded() -> None:
    """should_stop() returns True once cost cap is crossed."""
    tracker = CostTracker(cost_cap_usd=0.10)
    assert not tracker.should_stop()

    # Accumulate cost beyond cap WITHOUT raising (directly mutate internal state)
    # We test this by recording just under cap, then forcing overage
    tracker.record(input_tokens=0, output_tokens=0, cost_usd=0.09)
    assert not tracker.should_stop()

    with pytest.raises(CostExceeded):
        tracker.record(input_tokens=0, output_tokens=0, cost_usd=0.02)

    # After cost exceeded, should_stop() must return True
    assert tracker.should_stop()


def test_wallclock_exceeded_raises() -> None:
    """WallclockExceeded is raised when wallclock cap passes."""
    tracker = CostTracker(cost_cap_usd=1.0, wallclock_cap_seconds=0.001)
    time.sleep(0.01)  # Wait longer than the 1ms cap

    with pytest.raises(WallclockExceeded):
        tracker.record(input_tokens=1, output_tokens=1, cost_usd=0.0)


def test_cost_tracker_fallback_pricing_without_cost_usd() -> None:
    """When cost_usd=None, tracker uses internal pricing fallback."""
    tracker = CostTracker(cost_cap_usd=100.0)
    # Should not raise; just accumulates
    tracker.record(input_tokens=1000, output_tokens=500, cost_usd=None)
    usage = tracker.usage
    assert usage.cost_usd > 0.0  # some cost computed

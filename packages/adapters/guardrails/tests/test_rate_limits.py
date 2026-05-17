"""Tests for RateLimitTracker — sliding window rate limiting.

Covers:
  - test_remap_24h_limit: 4th remap in 24h → RateLimited
  - test_mapping_24h_limit: 2nd mapping in 24h → RateLimited
  - test_mapping_24h_limit_override: override bypasses limit
  - Sliding window: events outside window don't count
"""

from __future__ import annotations

import sqlite3

import pytest

from open_banca_guardrails.errors import RateLimited
from open_banca_guardrails.rate_limits import RateLimitTracker

from .conftest import FakeClock

# ------------------------------------------------------------------ #
# test_remap_24h_limit                                                  #
# ------------------------------------------------------------------ #


def test_remap_24h_limit_first_three_succeed(rate: RateLimitTracker, clock: FakeClock) -> None:
    """First 3 remaps in 24h succeed."""
    for _ in range(3):
        clock.tick(minutes=1)
        rate.record_and_check("banco_general", "remap")  # no raise


def test_remap_24h_limit_fourth_raises(rate: RateLimitTracker, clock: FakeClock) -> None:
    """4th remap within 24h window raises RateLimited."""
    for _ in range(3):
        clock.tick(minutes=1)
        rate.record_and_check("banco_general", "remap")

    clock.tick(minutes=1)
    with pytest.raises(RateLimited) as exc_info:
        rate.record_and_check("banco_general", "remap")

    err = exc_info.value
    assert err.bank == "banco_general"
    assert err.op_type == "remap"
    assert err.limit == 3
    assert err.window_hours == 24


def test_remap_24h_limit_after_window_resets(rate: RateLimitTracker, clock: FakeClock) -> None:
    """After sliding window passes, remap is allowed again."""
    for _ in range(3):
        clock.tick(hours=1)
        rate.record_and_check("banco_general", "remap")

    # Advance past 24h from the first event — sliding window should shrink
    clock.tick(hours=22)  # now 23h after last event, 25h after first
    rate.record_and_check("banco_general", "remap")  # no raise


def test_remap_24h_limit_different_banks_independent(
    rate: RateLimitTracker, clock: FakeClock
) -> None:
    """Rate limits are per bank — banco_general exhausted doesn't affect other."""
    for _ in range(3):
        clock.tick(minutes=1)
        rate.record_and_check("banco_general", "remap")

    # Different bank should be unaffected
    clock.tick(minutes=1)
    rate.record_and_check("banistmo", "remap")  # no raise


# ------------------------------------------------------------------ #
# test_mapping_24h_limit                                                #
# ------------------------------------------------------------------ #


def test_mapping_24h_limit_first_succeeds(rate: RateLimitTracker, clock: FakeClock) -> None:
    """First mapping in 24h succeeds."""
    clock.tick(minutes=1)
    rate.record_and_check("banco_general", "mapping")  # no raise


def test_mapping_24h_limit_second_raises(rate: RateLimitTracker, clock: FakeClock) -> None:
    """2nd mapping in 24h without override raises RateLimited."""
    clock.tick(minutes=1)
    rate.record_and_check("banco_general", "mapping")

    clock.tick(hours=2)
    with pytest.raises(RateLimited) as exc_info:
        rate.record_and_check("banco_general", "mapping")

    err = exc_info.value
    assert err.op_type == "mapping"
    assert err.limit == 1


def test_mapping_24h_limit_override_bypasses(mem_db: sqlite3.Connection, clock: FakeClock) -> None:
    """mapping_limit_override=True allows multiple mappings."""
    tracker = RateLimitTracker(
        conn=mem_db,
        remap_limit_24h=3,
        mapping_limit_24h=1,
        mapping_limit_override=True,
        clock=clock,
    )
    clock.tick(minutes=1)
    tracker.record_and_check("banco_general", "mapping")
    clock.tick(hours=1)
    tracker.record_and_check("banco_general", "mapping")  # no raise


def test_check_does_not_record(rate: RateLimitTracker, clock: FakeClock) -> None:
    """check() does not record an event; count stays the same after checking."""
    clock.tick(minutes=1)
    rate.record_and_check("banco_general", "remap")
    assert rate.count_recent("banco_general", "remap") == 1

    # Calling check() twice doesn't advance the count
    rate.check("banco_general", "remap")
    rate.check("banco_general", "remap")
    assert rate.count_recent("banco_general", "remap") == 1


def test_rate_limited_error_has_retry_after(rate: RateLimitTracker, clock: FakeClock) -> None:
    """RateLimited error carries a positive retry_after_seconds."""
    # Fill up the remap limit
    for _ in range(3):
        clock.tick(minutes=30)
        rate.record_and_check("banco_general", "remap")

    clock.tick(minutes=1)
    with pytest.raises(RateLimited) as exc_info:
        rate.record_and_check("banco_general", "remap")

    # Should have a positive retry_after indicating when the oldest event expires
    assert exc_info.value.retry_after_seconds > 0

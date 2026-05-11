"""Shared fixtures for guardrails tests."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from open_banca_guardrails.budget import BudgetEnforcer
from open_banca_guardrails.circuit_breaker import CircuitBreaker
from open_banca_guardrails.db import open_db
from open_banca_guardrails.enforcer import GuardrailEnforcer
from open_banca_guardrails.rate_limits import RateLimitTracker


class FakeClock:
    """Deterministic clock for tests; advance via ``tick()`` or ``set()``."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def tick(self, **kwargs: float) -> None:
        """Advance the clock by the given timedelta keyword arguments."""
        self._now += timedelta(**kwargs)

    def set(self, dt: datetime) -> None:
        """Set the clock to a specific datetime."""
        self._now = dt


@pytest.fixture
def mem_db() -> sqlite3.Connection:
    """In-memory guardrails SQLite database with schema applied."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    conn = open_db(path)
    return conn


@pytest.fixture
def clock() -> FakeClock:
    """Fresh FakeClock starting at 2025-01-01 12:00 UTC."""
    return FakeClock()


@pytest.fixture
def budget(mem_db: sqlite3.Connection) -> BudgetEnforcer:
    """BudgetEnforcer with default caps."""
    return BudgetEnforcer(
        conn=mem_db,
        llm_job_cap_usd=Decimal("0.50"),
        scrape_cap_usd=Decimal("0.10"),
    )


@pytest.fixture
def rate(mem_db: sqlite3.Connection, clock: FakeClock) -> RateLimitTracker:
    """RateLimitTracker with default limits and fake clock."""
    return RateLimitTracker(
        conn=mem_db,
        remap_limit_24h=3,
        mapping_limit_24h=1,
        mapping_limit_override=False,
        clock=clock,
    )


@pytest.fixture
def cb(mem_db: sqlite3.Connection, clock: FakeClock) -> CircuitBreaker:
    """CircuitBreaker with default thresholds and fake clock."""
    return CircuitBreaker(
        conn=mem_db,
        login_fail_limit=2,
        cooldown_hours=1,
        clock=clock,
    )


@pytest.fixture
def enforcer(
    mem_db: sqlite3.Connection,
    clock: FakeClock,
) -> GuardrailEnforcer:
    """GuardrailEnforcer wired with shared in-memory DB and fake clock."""
    b = BudgetEnforcer(
        conn=mem_db,
        llm_job_cap_usd=Decimal("0.50"),
        scrape_cap_usd=Decimal("0.10"),
    )
    r = RateLimitTracker(
        conn=mem_db,
        remap_limit_24h=3,
        mapping_limit_24h=1,
        mapping_limit_override=False,
        clock=clock,
    )
    c = CircuitBreaker(
        conn=mem_db,
        login_fail_limit=2,
        cooldown_hours=1,
        clock=clock,
    )
    return GuardrailEnforcer(budget=b, rate=r, circuit=c)

"""Tests for CircuitBreaker — per-(bank, credential) circuit state.

Covers:
  - test_circuit_breaker_login: 2 consecutive failures → CircuitOpen
  - test_circuit_reset_after_1h: after 1h passes → HALF_OPEN, probe allowed
  - Half-open: success → CLOSED, failure → re-OPEN
  - Success resets consecutive failure counter
"""

from __future__ import annotations

import pytest

from open_banca_guardrails.circuit_breaker import CircuitBreaker, CircuitState
from open_banca_guardrails.errors import CircuitOpen

from .conftest import FakeClock

BANK = "banco_general"
CRED = "cred-001"


# ------------------------------------------------------------------ #
# test_circuit_breaker_login                                            #
# ------------------------------------------------------------------ #


def test_circuit_initially_closed(cb: CircuitBreaker) -> None:
    """New bank/credential starts with CLOSED circuit."""
    assert cb.state(BANK, CRED) == CircuitState.CLOSED


def test_circuit_one_failure_stays_closed(cb: CircuitBreaker) -> None:
    """Single login failure does not open the circuit."""
    cb.record_failure(BANK, CRED)
    assert cb.state(BANK, CRED) == CircuitState.CLOSED
    cb.check(BANK, CRED)  # no raise


def test_circuit_two_failures_open(cb: CircuitBreaker) -> None:
    """2 consecutive login failures → OPEN state."""
    cb.record_failure(BANK, CRED)
    new_state = cb.record_failure(BANK, CRED)
    assert new_state == CircuitState.OPEN
    assert cb.state(BANK, CRED) == CircuitState.OPEN


def test_circuit_open_raises_circuit_open(cb: CircuitBreaker) -> None:
    """check() raises CircuitOpen when circuit is OPEN."""
    cb.record_failure(BANK, CRED)
    cb.record_failure(BANK, CRED)
    with pytest.raises(CircuitOpen) as exc_info:
        cb.check(BANK, CRED)
    err = exc_info.value
    assert err.bank == BANK
    assert err.credential_ref == CRED
    assert err.cooldown_remaining_seconds > 0


def test_circuit_success_resets_failures(cb: CircuitBreaker) -> None:
    """Successful login after one failure resets the counter."""
    cb.record_failure(BANK, CRED)
    cb.record_success(BANK, CRED)
    # Should need 2 failures again from the reset
    cb.record_failure(BANK, CRED)
    cb.check(BANK, CRED)  # still CLOSED after only 1 failure
    assert cb.state(BANK, CRED) == CircuitState.CLOSED


# ------------------------------------------------------------------ #
# test_circuit_reset_after_1h                                           #
# ------------------------------------------------------------------ #


def test_circuit_reset_after_1h(cb: CircuitBreaker, clock: FakeClock) -> None:
    """After 1h cooldown, OPEN → HALF_OPEN; probe attempt is allowed."""
    cb.record_failure(BANK, CRED)
    cb.record_failure(BANK, CRED)
    assert cb.state(BANK, CRED) == CircuitState.OPEN

    # Before cooldown: still OPEN
    clock.tick(minutes=59)
    assert cb.state(BANK, CRED) == CircuitState.OPEN

    # After cooldown: transitions to HALF_OPEN
    clock.tick(minutes=2)
    assert cb.state(BANK, CRED) == CircuitState.HALF_OPEN
    cb.check(BANK, CRED)  # no raise in HALF_OPEN


def test_half_open_success_closes(cb: CircuitBreaker, clock: FakeClock) -> None:
    """Successful login during HALF_OPEN → CLOSED."""
    cb.record_failure(BANK, CRED)
    cb.record_failure(BANK, CRED)
    clock.tick(hours=2)
    assert cb.state(BANK, CRED) == CircuitState.HALF_OPEN

    cb.record_success(BANK, CRED)
    assert cb.state(BANK, CRED) == CircuitState.CLOSED
    cb.check(BANK, CRED)  # no raise


def test_half_open_failure_reopens(cb: CircuitBreaker, clock: FakeClock) -> None:
    """Login failure during HALF_OPEN re-opens the circuit."""
    cb.record_failure(BANK, CRED)
    cb.record_failure(BANK, CRED)
    clock.tick(hours=2)
    assert cb.state(BANK, CRED) == CircuitState.HALF_OPEN

    new_state = cb.record_failure(BANK, CRED)
    assert new_state == CircuitState.OPEN
    with pytest.raises(CircuitOpen):
        cb.check(BANK, CRED)


def test_circuit_separate_credentials_independent(
    cb: CircuitBreaker,
) -> None:
    """Circuit state is per credential — one credential doesn't affect another."""
    cb.record_failure(BANK, "cred-A")
    cb.record_failure(BANK, "cred-A")
    assert cb.state(BANK, "cred-A") == CircuitState.OPEN
    # Different credential is unaffected
    assert cb.state(BANK, "cred-B") == CircuitState.CLOSED
    cb.check(BANK, "cred-B")  # no raise

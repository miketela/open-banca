"""Per-(bank, credential) circuit breaker with SQLite-backed state.

States:
    CLOSED     — normal operation; failures are counted.
    OPEN       — blocked; raised CircuitOpen until cooldown expires.
    HALF_OPEN  — one probe allowed; success → CLOSED, failure → OPEN again.

Trigger: 2 consecutive login failures → OPEN for 1 hour.
After 1 hour: → HALF_OPEN (one attempt allowed).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from open_banca_domain.ports.clock_port import ClockPort

from open_banca_guardrails.errors import CircuitOpen


class CircuitState(StrEnum):
    """Circuit breaker state machine states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _SystemClock:
    """Real wall-clock implementation of ClockPort."""

    def now(self) -> datetime:
        """Return current UTC time."""
        return datetime.now(tz=UTC)


class CircuitBreaker:
    """SQLite-backed per-(bank, credential) circuit breaker.

    Args:
        conn: Open sqlite3 connection (guardrails DB).
        login_fail_limit: Consecutive failures to trigger OPEN state.
        cooldown_hours: Hours the circuit stays OPEN before → HALF_OPEN.
        clock: Clock implementation (inject for testing).
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        login_fail_limit: int = 2,
        cooldown_hours: int = 1,
        clock: ClockPort | None = None,
    ) -> None:
        self._conn = conn
        self._fail_limit = login_fail_limit
        self._cooldown_hours = cooldown_hours
        self._clock: ClockPort = clock or _SystemClock()  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def state(self, bank: str, credential_ref: str) -> CircuitState:
        """Return the current circuit state, transitioning OPEN → HALF_OPEN if cooldown passed.

        Args:
            bank: Bank identifier.
            credential_ref: Credential reference string.

        Returns:
            Current ``CircuitState``.
        """
        row = self._conn.execute(
            "SELECT state, opened_at FROM circuit_breaker WHERE bank = ? AND credential_ref = ?",
            (bank, credential_ref),
        ).fetchone()
        if row is None:
            return CircuitState.CLOSED
        raw_state, opened_at_str = row
        state = CircuitState(raw_state)
        if state == CircuitState.OPEN and opened_at_str:
            opened_at = datetime.strptime(opened_at_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
            if self._clock.now() >= opened_at + timedelta(hours=self._cooldown_hours):
                self._transition(bank, credential_ref, CircuitState.HALF_OPEN)
                return CircuitState.HALF_OPEN
        return state

    def check(self, bank: str, credential_ref: str) -> None:
        """Raise ``CircuitOpen`` if the circuit is OPEN (not yet cooled down).

        Args:
            bank: Bank identifier.
            credential_ref: Credential reference string.

        Raises:
            CircuitOpen: If the circuit is in OPEN state.
        """
        current = self.state(bank, credential_ref)
        if current == CircuitState.OPEN:
            cooldown_remaining = self._cooldown_remaining(bank, credential_ref)
            raise CircuitOpen(
                bank=bank,
                credential_ref=credential_ref,
                cooldown_remaining_seconds=cooldown_remaining,
            )

    def record_failure(self, bank: str, credential_ref: str) -> CircuitState:
        """Record a login failure and potentially open the circuit.

        Args:
            bank: Bank identifier.
            credential_ref: Credential reference string.

        Returns:
            New circuit state after recording the failure.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT state, consecutive_failures FROM circuit_breaker "
                "WHERE bank = ? AND credential_ref = ?",
                (bank, credential_ref),
            ).fetchone()

            if row is None:
                consecutive = 1
                new_state = CircuitState.CLOSED
            else:
                existing_state, existing_failures = row
                # Reset half-open failure immediately to OPEN
                if CircuitState(existing_state) == CircuitState.HALF_OPEN:
                    consecutive = self._fail_limit  # immediately re-open
                else:
                    consecutive = existing_failures + 1
                new_state = CircuitState(existing_state)

            now_str = self._clock.now().strftime("%Y-%m-%dT%H:%M:%S")

            if consecutive >= self._fail_limit:
                new_state = CircuitState.OPEN
                self._conn.execute(
                    """
                    INSERT INTO circuit_breaker
                        (bank, credential_ref, state, opened_at, consecutive_failures)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(bank, credential_ref) DO UPDATE SET
                        state = excluded.state,
                        opened_at = excluded.opened_at,
                        consecutive_failures = excluded.consecutive_failures
                    """,
                    (bank, credential_ref, str(new_state), now_str, consecutive),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO circuit_breaker
                        (bank, credential_ref, state, opened_at, consecutive_failures)
                    VALUES (?, ?, 'closed', NULL, ?)
                    ON CONFLICT(bank, credential_ref) DO UPDATE SET
                        state = 'closed',
                        consecutive_failures = excluded.consecutive_failures
                    """,
                    (bank, credential_ref, consecutive),
                )

            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return new_state

    def record_success(self, bank: str, credential_ref: str) -> CircuitState:
        """Record a successful login; resets failures and closes the circuit.

        Args:
            bank: Bank identifier.
            credential_ref: Credential reference string.

        Returns:
            New circuit state (always CLOSED).
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                """
                INSERT INTO circuit_breaker
                    (bank, credential_ref, state, opened_at, consecutive_failures)
                VALUES (?, ?, 'closed', NULL, 0)
                ON CONFLICT(bank, credential_ref) DO UPDATE SET
                    state = 'closed',
                    opened_at = NULL,
                    consecutive_failures = 0
                """,
                (bank, credential_ref),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return CircuitState.CLOSED

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _transition(self, bank: str, credential_ref: str, new_state: CircuitState) -> None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                """
                INSERT INTO circuit_breaker
                    (bank, credential_ref, state, opened_at, consecutive_failures)
                VALUES (?, ?, ?, NULL, 0)
                ON CONFLICT(bank, credential_ref) DO UPDATE SET
                    state = excluded.state
                """,
                (bank, credential_ref, str(new_state)),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def _cooldown_remaining(self, bank: str, credential_ref: str) -> float:
        row = self._conn.execute(
            "SELECT opened_at FROM circuit_breaker WHERE bank = ? AND credential_ref = ?",
            (bank, credential_ref),
        ).fetchone()
        if not row or not row[0]:
            return 0.0
        opened_at = datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
        expiry = opened_at + timedelta(hours=self._cooldown_hours)
        remaining = (expiry - self._clock.now()).total_seconds()
        return max(0.0, remaining)

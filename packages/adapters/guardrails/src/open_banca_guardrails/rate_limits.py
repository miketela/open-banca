"""Rate limit tracking with sliding window and SQLite-backed counters.

Tracks operation events per bank using a sliding window (events in the last
``N`` hours).  This avoids fixed-bucket boundary effects.

Rules (all configurable):
  - max 3 remap attempts per bank per 24h.
  - max 1 mapping run per bank per 24h (override via config).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from open_banca_domain.ports.clock_port import ClockPort

from open_banca_guardrails.errors import RateLimited


class _SystemClock:
    """Real wall-clock implementation of ClockPort."""

    def now(self) -> datetime:
        """Return current UTC time."""
        return datetime.now(tz=UTC)


class RateLimitTracker:
    """SQLite-backed sliding-window rate limiter.

    Each call to ``record_and_check`` appends an event row and then counts
    events in the sliding window.  If the count exceeds the limit, the event
    is rolled back and ``RateLimited`` is raised.

    Args:
        conn: Open sqlite3 connection (guardrails DB).
        remap_limit_24h: Max remap attempts per bank per 24h window.
        mapping_limit_24h: Max mapping runs per bank per 24h window.
        mapping_limit_override: If True, skip the mapping limit check.
        clock: Clock implementation (inject for testing).
    """

    WINDOW_HOURS = 24

    def __init__(
        self,
        conn: sqlite3.Connection,
        remap_limit_24h: int = 3,
        mapping_limit_24h: int = 1,
        mapping_limit_override: bool = False,
        clock: ClockPort | None = None,
    ) -> None:
        self._conn = conn
        self._remap_limit = remap_limit_24h
        self._mapping_limit = mapping_limit_24h
        self._mapping_override = mapping_limit_override
        self._clock: ClockPort = clock or _SystemClock()  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def count_recent(self, bank: str, op_type: str, window_hours: int = 24) -> int:
        """Return how many events exist in the sliding window.

        Args:
            bank: Bank identifier.
            op_type: Operation type.
            window_hours: Window size in hours.

        Returns:
            Count of events.
        """
        cutoff = self._clock.now() - timedelta(hours=window_hours)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
        row = self._conn.execute(
            "SELECT COUNT(*) FROM rate_events WHERE bank = ? AND op_type = ? "
            "AND occurred_at > ?",
            (bank, op_type, cutoff_str),
        ).fetchone()
        return int(row[0]) if row else 0

    def check(self, bank: str, op_type: str) -> None:
        """Check rate limit without recording; raise if already at limit.

        Args:
            bank: Bank identifier.
            op_type: Operation type (``"remap"`` or ``"mapping"``).

        Raises:
            RateLimited: If already at or over the limit.
        """
        limit, window = self._limit_for(op_type)
        if limit is None:
            return
        count = self.count_recent(bank, op_type, window)
        if count >= limit:
            oldest_ts = self._oldest_event_in_window(bank, op_type, window)
            retry_after = self._retry_after(oldest_ts, window)
            raise RateLimited(
                bank=bank,
                op_type=op_type,
                limit=limit,
                window_hours=window,
                retry_after_seconds=retry_after,
            )

    def record_and_check(self, bank: str, op_type: str) -> None:
        """Record an event and raise ``RateLimited`` if the limit is exceeded.

        The event is rolled back if the limit would be exceeded, so the count
        remains accurate even after a refusal.

        Args:
            bank: Bank identifier.
            op_type: Operation type (``"remap"`` or ``"mapping"``).

        Raises:
            RateLimited: If recording this event would exceed the limit.
        """
        limit, window = self._limit_for(op_type)
        now_str = self._clock.now().strftime("%Y-%m-%dT%H:%M:%S")

        if limit is None:
            # No limit configured for this op_type — just record.
            self._insert_event(bank, op_type, now_str)
            return

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "INSERT INTO rate_events (bank, op_type, occurred_at) VALUES (?, ?, ?)",
                (bank, op_type, now_str),
            )
            cutoff = self._clock.now() - timedelta(hours=window)
            cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
            row = self._conn.execute(
                "SELECT COUNT(*) FROM rate_events WHERE bank = ? AND op_type = ? "
                "AND occurred_at > ?",
                (bank, op_type, cutoff_str),
            ).fetchone()
            count = int(row[0]) if row else 0

            if count > limit:
                self._conn.execute("ROLLBACK")
                oldest_ts = self._oldest_event_in_window(bank, op_type, window)
                retry_after = self._retry_after(oldest_ts, window)
                raise RateLimited(
                    bank=bank,
                    op_type=op_type,
                    limit=limit,
                    window_hours=window,
                    retry_after_seconds=retry_after,
                )
            self._conn.execute("COMMIT")
        except RateLimited:
            raise
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _limit_for(self, op_type: str) -> tuple[int | None, int]:
        """Return (limit, window_hours) for the given op_type.

        Returns (None, window) when no limit applies (unrestricted).
        """
        if op_type == "remap":
            return self._remap_limit, self.WINDOW_HOURS
        if op_type == "mapping":
            if self._mapping_override:
                return None, self.WINDOW_HOURS
            return self._mapping_limit, self.WINDOW_HOURS
        # Unknown op types: record but don't limit.
        return None, self.WINDOW_HOURS

    def _insert_event(self, bank: str, op_type: str, ts: str) -> None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "INSERT INTO rate_events (bank, op_type, occurred_at) VALUES (?, ?, ?)",
                (bank, op_type, ts),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def _oldest_event_in_window(
        self, bank: str, op_type: str, window_hours: int
    ) -> datetime | None:
        cutoff = self._clock.now() - timedelta(hours=window_hours)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
        row = self._conn.execute(
            "SELECT MIN(occurred_at) FROM rate_events WHERE bank = ? AND op_type = ? "
            "AND occurred_at > ?",
            (bank, op_type, cutoff_str),
        ).fetchone()
        if row and row[0]:
            return datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=UTC
            )
        return None

    def _retry_after(self, oldest: datetime | None, window_hours: int) -> float:
        if oldest is None:
            return 0.0
        expiry = oldest + timedelta(hours=window_hours)
        remaining = (expiry - self._clock.now()).total_seconds()
        return max(0.0, remaining)

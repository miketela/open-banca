"""Webhook outbox repository — SQLite persistence for the transactional outbox pattern.

Uses plain SQLite (not SQLCipher) because the outbox contains no secrets:
payloads are already redacted before storage (REQ-016).

Table schema created on first connection via _ensure_schema().
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


# Retry backoff delays in seconds per attempt index (0-based)
# Spec: 15s, 1m, 5m, 30m, 2h, 6h  (6 total attempts → max 6 in ~24h)
RETRY_DELAYS_SECONDS = [15, 60, 300, 1800, 7200, 21600]
MAX_ATTEMPTS = len(RETRY_DELAYS_SECONDS)

# 4xx status codes that should not be retried (park to DLQ immediately)
NO_RETRY_4XX = {400, 401, 403, 410}

_CREATE_OUTBOX = """
CREATE TABLE IF NOT EXISTS webhook_outbox (
    id          TEXT PRIMARY KEY,
    event_id    TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    target_url  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    signature   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

_CREATE_NONCE_STORE = """
CREATE TABLE IF NOT EXISTS webhook_nonces (
    nonce       TEXT NOT NULL,
    ts          INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (nonce, ts)
);
"""

_CREATE_INDEX_STATUS = """
CREATE INDEX IF NOT EXISTS idx_webhook_outbox_status
    ON webhook_outbox(status, next_attempt_at);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_OUTBOX)
    conn.execute(_CREATE_NONCE_STORE)
    conn.execute(_CREATE_INDEX_STATUS)
    conn.commit()


class WebhookOutboxRepository:
    """SQLite-backed webhook outbox.

    Thread-safe via a threading.Lock.  Each instance owns its connection.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        _ensure_schema(self._conn)

    # ── Write ─────────────────────────────────────────────────────────────────

    def enqueue(  # noqa: PLR0913
        self,
        *,
        outbox_id: str,
        event_id: str,
        event_type: str,
        target_url: str,
        payload: dict[str, Any],
        signature: str,
    ) -> None:
        """Insert a new outbox record with status=pending."""
        now = _utcnow_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO webhook_outbox
                    (id, event_id, event_type, target_url, payload,
                     signature, status, attempts, next_attempt_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)
                """,
                (
                    outbox_id,
                    event_id,
                    event_type,
                    target_url,
                    json.dumps(payload),
                    signature,
                    now,  # next_attempt_at = now (ready immediately)
                    now,
                    now,
                ),
            )
            self._conn.commit()

    def mark_delivered(self, outbox_id: str) -> None:
        """Mark record as delivered."""
        now = _utcnow_iso()
        with self._lock:
            self._conn.execute(
                "UPDATE webhook_outbox SET status='delivered', updated_at=? WHERE id=?",
                (now, outbox_id),
            )
            self._conn.commit()

    def mark_failed(self, outbox_id: str, error: str, attempt: int) -> None:
        """Increment attempt count and schedule next retry or park to DLQ."""
        now = _utcnow_iso()
        new_attempts = attempt + 1

        if new_attempts >= MAX_ATTEMPTS:
            # Move to dead letter queue
            with self._lock:
                self._conn.execute(
                    """
                    UPDATE webhook_outbox
                    SET status='dead', attempts=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (new_attempts, error, now, outbox_id),
                )
                self._conn.commit()
        else:
            # Schedule next retry
            delay = RETRY_DELAYS_SECONDS[new_attempts]
            next_at = (
                datetime.now(tz=UTC) + timedelta(seconds=delay)
            ).isoformat()
            with self._lock:
                self._conn.execute(
                    """
                    UPDATE webhook_outbox
                    SET status='pending', attempts=?, last_error=?,
                        next_attempt_at=?, updated_at=?
                    WHERE id=?
                    """,
                    (new_attempts, error, next_at, now, outbox_id),
                )
                self._conn.commit()

    def park_to_dlq(self, outbox_id: str, error: str, attempt: int) -> None:
        """Park directly to DLQ (non-retryable 4xx)."""
        now = _utcnow_iso()
        with self._lock:
            self._conn.execute(
                """
                UPDATE webhook_outbox
                SET status='dead', attempts=?, last_error=?, updated_at=?
                WHERE id=?
                """,
                (attempt + 1, error, now, outbox_id),
            )
            self._conn.commit()

    def mark_replaying(self, outbox_id: str) -> None:
        """Reset a dead record back to pending for replay."""
        now = _utcnow_iso()
        with self._lock:
            self._conn.execute(
                """
                UPDATE webhook_outbox
                SET status='pending', attempts=0, next_attempt_at=?, updated_at=?
                WHERE id=? AND status='dead'
                """,
                (now, now, outbox_id),
            )
            self._conn.commit()

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_pending(self) -> list[dict[str, Any]]:
        """Return all pending records ready for delivery (next_attempt_at <= now)."""
        now = _utcnow_iso()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, event_id, event_type, target_url, payload,
                       signature, attempts
                FROM webhook_outbox
                WHERE status = 'pending' AND next_attempt_at <= ?
                ORDER BY next_attempt_at ASC
                """,
                (now,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_dlq(self) -> list[dict[str, Any]]:
        """Return all dead-letter records."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, event_id, event_type, target_url, payload,
                       signature, status, attempts, last_error, created_at, updated_at
                FROM webhook_outbox
                WHERE status = 'dead'
                ORDER BY updated_at DESC
                """,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_by_id(self, outbox_id: str) -> dict[str, Any] | None:
        """Get a single outbox record by ID."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM webhook_outbox WHERE id=?",
                (outbox_id,),
            ).fetchone()
        return dict(row) if row else None

    # ── Nonce store ───────────────────────────────────────────────────────────

    def record_nonce(self, nonce: str, ts: int) -> bool:
        """Record a (nonce, ts) pair. Returns False if already seen (anti-replay)."""
        now = _utcnow_iso()
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO webhook_nonces (nonce, ts, created_at) VALUES (?, ?, ?)",
                    (nonce, ts, now),
                )
                self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def evict_old_nonces(self, max_age_seconds: int = 120) -> int:
        """Remove nonces older than max_age_seconds. Returns count removed."""
        cutoff = (
            datetime.now(tz=UTC) - timedelta(seconds=max_age_seconds)
        ).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM webhook_nonces WHERE created_at < ?",
                (cutoff,),
            )
            self._conn.commit()
        return cur.rowcount


__all__ = [
    "WebhookOutboxRepository",
    "RETRY_DELAYS_SECONDS",
    "MAX_ATTEMPTS",
    "NO_RETRY_4XX",
]

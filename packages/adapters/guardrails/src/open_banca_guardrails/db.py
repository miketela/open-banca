"""Plain SQLite database helper for guardrails counters.

Guardrails state (budget totals, rate-limit events, circuit breaker) does not
contain PII, so it uses stdlib sqlite3 — no SQLCipher dependency required.

``BEGIN IMMEDIATE`` is used for all writes to prevent write-write races in
multi-process deployments (e.g., a Temporal worker pool).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_DDL = """\
CREATE TABLE IF NOT EXISTS budget_consumed (
    job_id      TEXT    NOT NULL,
    op_type     TEXT    NOT NULL,
    amount_usd  REAL    NOT NULL,  -- cumulative consumed, updated on record()
    PRIMARY KEY (job_id, op_type)
);

CREATE TABLE IF NOT EXISTS rate_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bank        TEXT    NOT NULL,
    op_type     TEXT    NOT NULL,
    occurred_at TEXT    NOT NULL   -- ISO-8601 UTC timestamp
);
CREATE INDEX IF NOT EXISTS idx_rate_events_lookup
    ON rate_events (bank, op_type, occurred_at);

CREATE TABLE IF NOT EXISTS circuit_breaker (
    bank                    TEXT    NOT NULL,
    credential_ref          TEXT    NOT NULL,
    state                   TEXT    NOT NULL DEFAULT 'closed',
    opened_at               TEXT,   -- ISO-8601 UTC, NULL when closed
    consecutive_failures    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bank, credential_ref)
);
"""


def open_db(db_path: Path) -> sqlite3.Connection:
    """Open (or create) the guardrails SQLite database and apply DDL.

    The returned connection has ``isolation_level=None`` (autocommit) so that
    callers can manage transactions explicitly with ``BEGIN IMMEDIATE``.

    Args:
        db_path: Filesystem path to the SQLite file.

    Returns:
        An open ``sqlite3.Connection``.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.isolation_level = None  # autocommit; callers use explicit transactions
    conn.executescript(_DDL)
    return conn

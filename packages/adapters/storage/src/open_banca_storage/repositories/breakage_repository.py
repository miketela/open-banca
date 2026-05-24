"""BreakageRepository — persist BreakageEvent to audit_log and enqueue webhook."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from open_banca_domain.entities.breakage_event import BreakageEvent
from open_banca_storage.connection import Connection
from open_banca_storage.repositories.job_store import AuditLogger, WebhookOutbox


def _utcnow_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(tz=UTC).isoformat()


class BreakageRepository:
    """Atomically persist a BreakageEvent and enqueue its ``job.failed`` webhook.

    Implements ``BreakageEventPort`` from the domain layer.

    Both the audit_log row and the webhook_outbox row are written in a single
    ``conn.commit()`` call so they succeed or fail together.

    Args:
        conn: Open SQLCipher connection with key applied and schema migrated.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._audit = AuditLogger(conn)
        self._outbox = WebhookOutbox(conn)

    def notify(self, event: BreakageEvent, job_id: str) -> None:
        """Persist breakage event to audit_log and enqueue job.failed webhook.

        Args:
            event: The BreakageEvent domain entity emitted by the browser runner.
            job_id: The job identifier (same as event.job_id, passed explicitly
                    for clarity and to satisfy the port interface).
        """
        event_dict: dict[str, Any] = event.model_dump(mode="json")

        # ── 1. Audit log row ──────────────────────────────────────────────────
        audit_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO audit_log (id, actor, action, entity_type, entity_id, at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                "browser_runner",
                "breakage.emitted",
                "job",
                job_id,
                _utcnow_iso(),
                json.dumps(event_dict),
            ),
        )

        # ── 2. Webhook outbox row ─────────────────────────────────────────────
        outbox_id = str(uuid.uuid4())
        payload: dict[str, Any] = {"job_id": job_id, "breakage": event_dict}
        self._conn.execute(
            """
            INSERT INTO webhook_outbox (
                id, job_id, event_type, payload_json, signature,
                attempts, next_retry_at, delivered_at, created_at
            ) VALUES (?, ?, ?, ?, ?, 0, NULL, NULL, ?)
            """,
            (
                outbox_id,
                job_id,
                "job.failed",
                json.dumps(payload),
                "",  # HMAC filled by Task #25 delivery worker
                _utcnow_iso(),
            ),
        )

        # ── 3. Single commit — atomic ─────────────────────────────────────────
        self._conn.commit()

"""WebhookDispatcher — implements EventBusPort.

Implements the transactional outbox pattern:
  1. publish(event) → enqueue in webhook_outbox (synchronous, no network)
  2. deliver_pending() → worker loop: query outbox, send via httpx, handle retry/DLQ

HMAC-SHA256 signature format (ADR-0011 / security review P0-3):
  X-OpenBanca-Signature: t=<unix_ts>,v1=<hex_hmac>,n=<nonce>

Security requirements:
  - Window: 60 seconds (narrowed from 300s per P0-3 review).
  - Nonce: REQUIRED — every delivery gets a fresh UUID4 nonce.
  - Payloads are redacted (REQ-016) before storage and delivery.

Retry schedule: 15s, 1m, 5m, 30m, 2h, 6h — max 6 attempts (~24h total).
After max retries → status=dead (DLQ).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from open_banca_domain.entities.webhook_event import WebhookEvent

from open_banca_webhooks.outbox import NO_RETRY_4XX, WebhookOutboxRepository
from open_banca_webhooks.redact import redact_payload
from open_banca_webhooks.signature import sign_payload

logger = logging.getLogger(__name__)

_VERSION = "0.1.0"
_USER_AGENT = f"open-banca-webhook/{_VERSION}"

# Headers per ADR-0011
_HEADER_SIGNATURE = "X-OpenBanca-Signature"
_HEADER_EVENT = "X-OpenBanca-Event"
_HEADER_DELIVERY = "X-OpenBanca-Delivery"


class WebhookDispatcher:
    """Implements EventBusPort — enqueues and delivers webhook events.

    Args:
        target_url: Default delivery endpoint.  Can be overridden per-event
            if the event carries a target_url field (not in current domain model).
        secret: HMAC signing secret.
        outbox: Repository backing the outbox.  Defaults to in-memory SQLite.
        http_timeout: Timeout in seconds for HTTP delivery attempts.
        db_path: Path for SQLite outbox.  Ignored if outbox is provided.
    """

    def __init__(
        self,
        target_url: str,
        secret: str,
        *,
        outbox: WebhookOutboxRepository | None = None,
        http_timeout: float = 10.0,
        db_path: str | Path = ":memory:",
    ) -> None:
        self._target_url = target_url
        self._secret = secret
        self._outbox = outbox or WebhookOutboxRepository(db_path)
        self._http_timeout = http_timeout

    # ── EventBusPort interface ─────────────────────────────────────────────

    def publish(self, event: WebhookEvent) -> None:
        """Enqueue an event in the outbox. No network I/O.

        Payload is redacted before persistence (REQ-016).
        """
        outbox_id = str(uuid.uuid4())
        ts = int(time.time())
        nonce = str(uuid.uuid4())

        # Redact payload before storage
        safe_payload = redact_payload(dict(event.payload))

        body = json.dumps(
            {
                "id": event.id,
                "event_type": str(event.event_type),
                "job_id": event.job_id,
                "dispatched_at": event.dispatched_at.isoformat(),
                "payload": safe_payload,
            },
            separators=(",", ":"),
        )

        signature = sign_payload(self._secret, body, timestamp=ts, nonce=nonce)

        self._outbox.enqueue(
            outbox_id=outbox_id,
            event_id=event.id,
            event_type=str(event.event_type),
            target_url=self._target_url,
            payload={"_body": body},  # store pre-serialized body for delivery
            signature=signature,
        )
        logger.info(
            "webhook.enqueued outbox_id=%s event_type=%s event_id=%s",
            outbox_id,
            event.event_type,
            event.id,
        )

    def retry_pending(self) -> None:
        """Alias for deliver_pending() to satisfy EventBusPort protocol."""
        self.deliver_pending()

    # ── Worker loop ────────────────────────────────────────────────────────

    def deliver_pending(self) -> int:
        """Deliver all pending outbox records. Returns count of delivered records.

        Intended to be called from the worker loop or via direct invocation.
        """
        pending = self._outbox.get_pending()
        delivered_count = 0

        for record in pending:
            outbox_id: str = record["id"]
            attempt: int = record["attempts"]

            try:
                payload_data: dict[str, Any] = json.loads(record["payload"])
                body: str = payload_data.get("_body", json.dumps(payload_data))

                # Re-sign with fresh timestamp + nonce for delivery
                # (original signature in outbox is for audit trail only)
                ts = int(time.time())
                nonce = str(uuid.uuid4())
                delivery_signature = sign_payload(self._secret, body, timestamp=ts, nonce=nonce)

                headers = {
                    _HEADER_SIGNATURE: delivery_signature,
                    _HEADER_EVENT: record["event_type"],
                    _HEADER_DELIVERY: record["event_id"],
                    "Content-Type": "application/json; charset=utf-8",
                    "User-Agent": _USER_AGENT,
                }

                with httpx.Client(timeout=self._http_timeout) as client:
                    response = client.post(
                        record["target_url"],
                        content=body.encode("utf-8"),
                        headers=headers,
                    )

                if response.is_success:
                    self._outbox.mark_delivered(outbox_id)
                    delivered_count += 1
                    logger.info(
                        "webhook.delivered outbox_id=%s status=%d",
                        outbox_id,
                        response.status_code,
                    )
                elif response.status_code in NO_RETRY_4XX:
                    # Non-retryable 4xx → park to DLQ immediately
                    error = f"HTTP {response.status_code} (non-retryable)"
                    self._outbox.park_to_dlq(outbox_id, error, attempt)
                    logger.warning(
                        "webhook.dlq outbox_id=%s status=%d (non-retryable)",
                        outbox_id,
                        response.status_code,
                    )
                else:
                    error = f"HTTP {response.status_code}"
                    self._outbox.mark_failed(outbox_id, error, attempt)
                    logger.warning(
                        "webhook.failed outbox_id=%s status=%d attempt=%d",
                        outbox_id,
                        response.status_code,
                        attempt,
                    )

            except httpx.TimeoutException as exc:
                error = f"Timeout: {exc}"
                self._outbox.mark_failed(outbox_id, error, attempt)
                logger.warning(
                    "webhook.timeout outbox_id=%s attempt=%d",
                    outbox_id,
                    attempt,
                )
            except Exception as exc:
                error = f"Error: {exc}"
                self._outbox.mark_failed(outbox_id, error, attempt)
                logger.exception(
                    "webhook.error outbox_id=%s attempt=%d",
                    outbox_id,
                    attempt,
                )

        return delivered_count

    # ── DLQ operations ────────────────────────────────────────────────────

    def list_dlq(self) -> list[dict[str, Any]]:
        """Return all dead-letter records."""
        return self._outbox.get_dlq()

    def replay_dlq(self, outbox_id: str) -> bool:
        """Reset a DLQ record to pending for re-delivery.

        Returns True if the record was found and reset, False if not found
        or not in dead status.
        """
        record = self._outbox.get_by_id(outbox_id)
        if record is None or record.get("status") != "dead":
            return False
        self._outbox.mark_replaying(outbox_id)
        logger.info("webhook.dlq_replay outbox_id=%s", outbox_id)
        return True


__all__ = ["WebhookDispatcher"]

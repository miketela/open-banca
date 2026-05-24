"""HU08 orchestrator visibility: webhook retry + DLQ behavior (delegates to webhooks pkg)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx
from open_banca_domain.entities.webhook_event import WebhookEvent, WebhookEventType

from open_banca_webhooks.dispatcher import WebhookDispatcher
from open_banca_webhooks.outbox import MAX_ATTEMPTS, WebhookOutboxRepository

TARGET = "https://hooks.example.com/open-banca"
SECRET = "hu08-test-secret"


def _event() -> WebhookEvent:
    return WebhookEvent(
        id=str(uuid.uuid4()),
        event_type=WebhookEventType.JOB_FAILED,
        payload={"job_id": "hu08-job"},
        signature="",
        dispatched_at=datetime.now(UTC),
        job_id="hu08-job",
    )


@respx.mock
def test_receiver_down_retries_then_dlq() -> None:
    """Simulate receiver down: exponential retry schedule then DLQ."""
    outbox = WebhookOutboxRepository(":memory:")
    dispatcher = WebhookDispatcher(
        target_url=TARGET, secret=SECRET, outbox=outbox, http_timeout=2.0
    )
    dispatcher.publish(_event())
    respx.post(TARGET).mock(return_value=httpx.Response(503))

    outbox_id = outbox.get_pending()[0]["id"]
    for attempt in range(MAX_ATTEMPTS):
        dispatcher.deliver_pending()
        record = outbox.get_by_id(outbox_id)
        assert record is not None
        if attempt < MAX_ATTEMPTS - 1:
            assert record["status"] == "pending"
            # Force next attempt ready (avoid waiting real backoff in test)
            with outbox._lock:
                outbox._conn.execute(
                    "UPDATE webhook_outbox SET next_attempt_at=? WHERE id=?",
                    (datetime.now(UTC).isoformat(), outbox_id),
                )
                outbox._conn.commit()
        else:
            assert record["status"] == "dead"

    assert len(dispatcher.list_dlq()) == 1

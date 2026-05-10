"""Dispatcher tests: retry, DLQ, all 7 events, payload canary redaction."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx
from open_banca_domain.entities.webhook_event import WebhookEvent, WebhookEventType

from open_banca_webhooks.dispatcher import WebhookDispatcher
from open_banca_webhooks.outbox import MAX_ATTEMPTS, RETRY_DELAYS_SECONDS, WebhookOutboxRepository

TARGET_URL = "https://example.com/webhook"
SECRET = "test-secret-for-dispatcher-tests"


def _make_event(
    event_type: WebhookEventType = WebhookEventType.JOB_COMPLETED,
    payload: dict[str, object] | None = None,
) -> WebhookEvent:
    return WebhookEvent(
        id=str(uuid.uuid4()),
        event_type=event_type,
        payload=payload or {"job_id": "test-job-123"},
        signature="",  # Will be re-signed by dispatcher
        dispatched_at=datetime.now(UTC),
        job_id="test-job-123",
    )


def _make_dispatcher(outbox: WebhookOutboxRepository | None = None) -> WebhookDispatcher:
    return WebhookDispatcher(
        target_url=TARGET_URL,
        secret=SECRET,
        outbox=outbox or WebhookOutboxRepository(":memory:"),
        http_timeout=5.0,
    )


# ── test_retry_exponential ────────────────────────────────────────────────────

def test_retry_delays_schedule() -> None:
    """Verify retry schedule matches spec: 15s, 1m, 5m, 30m, 2h, 6h."""
    assert RETRY_DELAYS_SECONDS == [15, 60, 300, 1800, 7200, 21600]
    assert MAX_ATTEMPTS == 6


def test_retry_max_6_attempts_then_dlq() -> None:
    """After 6 failures, record should be in dead status (DLQ)."""
    outbox = WebhookOutboxRepository(":memory:")
    dispatcher = _make_dispatcher(outbox)
    event = _make_event()

    dispatcher.publish(event)

    # Simulate all 6 failed attempts by directly marking failed
    records = outbox.get_pending()
    assert len(records) == 1
    outbox_id = records[0]["id"]

    # Mark failed MAX_ATTEMPTS times
    for attempt in range(MAX_ATTEMPTS):
        outbox.mark_failed(outbox_id, f"error at attempt {attempt}", attempt)
        record = outbox.get_by_id(outbox_id)
        assert record is not None
        if attempt < MAX_ATTEMPTS - 1:
            assert record["status"] == "pending", f"Expected pending at attempt {attempt}"
        else:
            assert record["status"] == "dead", "Expected dead after max attempts"


def test_dlq_items_listed() -> None:
    """After max retries, item appears in DLQ listing."""
    outbox = WebhookOutboxRepository(":memory:")
    dispatcher = _make_dispatcher(outbox)
    event = _make_event()

    dispatcher.publish(event)
    records = outbox.get_pending()
    outbox_id = records[0]["id"]

    # Exhaust retries
    for attempt in range(MAX_ATTEMPTS):
        outbox.mark_failed(outbox_id, "simulated error", attempt)

    dlq = dispatcher.list_dlq()
    assert len(dlq) == 1
    assert dlq[0]["id"] == outbox_id
    assert dlq[0]["status"] == "dead"


def test_dlq_replay_resets_to_pending() -> None:
    """replay_dlq() should reset dead record to pending."""
    outbox = WebhookOutboxRepository(":memory:")
    dispatcher = _make_dispatcher(outbox)
    event = _make_event()

    dispatcher.publish(event)
    records = outbox.get_pending()
    outbox_id = records[0]["id"]

    # Move to DLQ
    for attempt in range(MAX_ATTEMPTS):
        outbox.mark_failed(outbox_id, "error", attempt)

    record = outbox.get_by_id(outbox_id)
    assert record is not None
    assert record["status"] == "dead"

    # Replay
    result = dispatcher.replay_dlq(outbox_id)
    assert result is True

    record = outbox.get_by_id(outbox_id)
    assert record is not None
    assert record["status"] == "pending"
    assert record["attempts"] == 0


def test_dlq_replay_nonexistent_returns_false() -> None:
    """replay_dlq() should return False for unknown IDs."""
    dispatcher = _make_dispatcher()
    result = dispatcher.replay_dlq("does-not-exist")
    assert result is False


def test_dlq_replay_pending_record_returns_false() -> None:
    """replay_dlq() should return False for pending (non-dead) records."""
    outbox = WebhookOutboxRepository(":memory:")
    dispatcher = _make_dispatcher(outbox)
    event = _make_event()
    dispatcher.publish(event)

    records = outbox.get_pending()
    outbox_id = records[0]["id"]

    result = dispatcher.replay_dlq(outbox_id)
    assert result is False


# ── test_all_7_events_emitable ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "event_type",
    [
        WebhookEventType.JOB_CREATED,
        WebhookEventType.JOB_OTP_REQUIRED,
        WebhookEventType.JOB_PROGRESS,
        WebhookEventType.JOB_COMPLETED,
        WebhookEventType.JOB_FAILED,
        WebhookEventType.JOB_REMAP_PROPOSED,
        WebhookEventType.JOB_HUMAN_REQUIRED,
    ],
)
def test_all_7_events_can_be_published(event_type: WebhookEventType) -> None:
    """Each of the 7 event types can be published to the outbox."""
    outbox = WebhookOutboxRepository(":memory:")
    dispatcher = _make_dispatcher(outbox)
    event = _make_event(event_type=event_type)

    dispatcher.publish(event)

    pending = outbox.get_pending()
    assert len(pending) == 1
    assert pending[0]["event_type"] == event_type.value


@respx.mock
def test_all_7_events_can_be_delivered() -> None:
    """Each event type can be published and then delivered successfully."""
    outbox = WebhookOutboxRepository(":memory:")
    dispatcher = _make_dispatcher(outbox)

    event_types = list(WebhookEventType)
    for et in event_types:
        dispatcher.publish(_make_event(event_type=et))

    # Mock delivery endpoint
    respx.post(TARGET_URL).mock(return_value=httpx.Response(200))

    delivered = dispatcher.deliver_pending()
    assert delivered == len(event_types)


# ── test_payload_canary_redact ─────────────────────────────────────────────────

def test_payload_canary_redact_before_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Payload containing SECRET_CANARY_VALUE must be redacted before outbox storage."""
    monkeypatch.setenv("SECRET_CANARY_VALUE", "super-secret-canary-xyz")

    outbox = WebhookOutboxRepository(":memory:")
    dispatcher = _make_dispatcher(outbox)

    event = _make_event(payload={"data": "super-secret-canary-xyz", "other": "safe-value"})
    dispatcher.publish(event)

    pending = outbox.get_pending()
    assert len(pending) == 1

    stored_payload = pending[0]["payload"]
    assert "super-secret-canary-xyz" not in stored_payload
    assert "[REDACTED]" in stored_payload


def test_payload_canary_redact_nested(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nested payload values containing canary must be redacted."""
    monkeypatch.setenv("SECRET_CANARY_VALUE", "nested-secret-value")

    outbox = WebhookOutboxRepository(":memory:")
    dispatcher = _make_dispatcher(outbox)

    event = _make_event(
        payload={"nested": {"key": "nested-secret-value"}, "safe": "ok"}
    )
    dispatcher.publish(event)

    pending = outbox.get_pending()
    stored_payload = pending[0]["payload"]
    assert "nested-secret-value" not in stored_payload


def test_payload_clean_not_modified(monkeypatch: pytest.MonkeyPatch) -> None:
    """Payload without canary values should be stored unchanged."""
    monkeypatch.setenv("SECRET_CANARY_VALUE", "sensitive-token-abc")

    outbox = WebhookOutboxRepository(":memory:")
    dispatcher = _make_dispatcher(outbox)

    event = _make_event(payload={"status": "completed", "count": 42})
    dispatcher.publish(event)

    pending = outbox.get_pending()
    stored_payload = pending[0]["payload"]
    assert "[REDACTED]" not in stored_payload


# ── integration: delivery with signature verification ─────────────────────────

@respx.mock
def test_deliver_pending_success() -> None:
    """Successful delivery should mark record as delivered."""
    outbox = WebhookOutboxRepository(":memory:")
    dispatcher = _make_dispatcher(outbox)
    event = _make_event()

    dispatcher.publish(event)
    respx.post(TARGET_URL).mock(return_value=httpx.Response(200))

    count = dispatcher.deliver_pending()
    assert count == 1

    # No more pending records
    assert len(outbox.get_pending()) == 0


@respx.mock
def test_deliver_pending_5xx_schedules_retry() -> None:
    """5xx response should schedule a retry."""
    outbox = WebhookOutboxRepository(":memory:")
    dispatcher = _make_dispatcher(outbox)
    event = _make_event()

    dispatcher.publish(event)
    respx.post(TARGET_URL).mock(return_value=httpx.Response(503))

    dispatcher.deliver_pending()

    # Since the retry delay is in the future, pending may be empty right now.
    # Check the record status directly via the private connection.
    all_records: list[dict[str, object]] = []
    with outbox._lock:
        rows = outbox._conn.execute(
            "SELECT id, status, attempts FROM webhook_outbox"
        ).fetchall()
        all_records = [dict(r) for r in rows]

    assert len(all_records) == 1
    assert all_records[0]["attempts"] == 1
    assert all_records[0]["status"] == "pending"


@respx.mock
def test_deliver_pending_non_retryable_4xx_goes_to_dlq() -> None:
    """Non-retryable 4xx (400, 401, 403, 410) should go directly to DLQ."""
    for status_code in [400, 401, 403, 410]:
        outbox = WebhookOutboxRepository(":memory:")
        dispatcher = _make_dispatcher(outbox)
        event = _make_event()

        dispatcher.publish(event)
        respx.post(TARGET_URL).mock(return_value=httpx.Response(status_code))

        dispatcher.deliver_pending()

        dlq = dispatcher.list_dlq()
        assert len(dlq) == 1, f"Expected DLQ record for HTTP {status_code}"
        assert dlq[0]["status"] == "dead"


@respx.mock
def test_delivered_record_has_correct_headers() -> None:
    """Delivered webhook must include required HMAC headers."""
    received_headers: dict[str, str] = {}

    def capture_headers(request: httpx.Request) -> httpx.Response:
        received_headers.update(dict(request.headers))
        return httpx.Response(200)

    outbox = WebhookOutboxRepository(":memory:")
    dispatcher = _make_dispatcher(outbox)
    event = _make_event()

    dispatcher.publish(event)
    respx.post(TARGET_URL).mock(side_effect=capture_headers)
    dispatcher.deliver_pending()

    assert "x-openbanca-signature" in received_headers
    assert "x-openbanca-event" in received_headers
    assert "x-openbanca-delivery" in received_headers

    sig = received_headers["x-openbanca-signature"]
    assert "t=" in sig
    assert "v1=" in sig
    assert "n=" in sig

"""Webhook endpoints: test, DLQ list, DLQ replay."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from open_banca_api.auth import verify_bearer
from open_banca_api.schemas.webhooks import (
    DlqItem,
    DlqListResponse,
    DlqReplayResponse,
    WebhookTestRequest,
    WebhookTestResponse,
)
from open_banca_domain.entities.webhook_event import WebhookEvent, WebhookEventType
from open_banca_webhooks.dispatcher import WebhookDispatcher

router = APIRouter(
    prefix="/webhooks",
    tags=["webhooks"],
    dependencies=[Depends(verify_bearer)],
)


def _get_dispatcher() -> WebhookDispatcher:
    """Return a WebhookDispatcher instance configured from environment.

    Raises HTTP 503 when target URL is not configured.
    """
    target_url = os.environ.get("OPEN_BANCA_WEBHOOK_TARGET_URL", "")
    secret = os.environ.get("OPEN_BANCA_WEBHOOK_SECRET", "dev-insecure-webhook-secret")
    db_path = os.environ.get("OPEN_BANCA_WEBHOOK_DB_PATH", "./webhook_outbox.db")

    if not target_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPEN_BANCA_WEBHOOK_TARGET_URL is not configured",
        )

    return WebhookDispatcher(target_url=target_url, secret=secret, db_path=db_path)


@router.post(
    "/test",
    response_model=WebhookTestResponse,
    status_code=status.HTTP_200_OK,
    summary="Fire a synthetic webhook",
    description=(
        "Sends a synthetic signed webhook event to the configured endpoint "
        "to verify connectivity and HMAC-SHA256 signature validation."
    ),
)
async def test_webhook(body: WebhookTestRequest) -> WebhookTestResponse:
    """POST /webhooks/test — enqueue a real signed test webhook."""
    dispatcher = _get_dispatcher()

    try:
        event_type = WebhookEventType(body.event_type)
    except ValueError as exc:
        valid = [e.value for e in WebhookEventType]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid event_type '{body.event_type}'. Valid values: {valid}",
        ) from exc

    event = WebhookEvent(
        id=str(uuid.uuid4()),
        event_type=event_type,
        payload={"test": True, "source": "POST /webhooks/test"},
        signature="",
        dispatched_at=datetime.now(UTC),
        job_id=None,
    )

    dispatcher.publish(event)

    return WebhookTestResponse(
        enqueued=True,
        outbox_id=event.id,
        detail=f"Test webhook enqueued for event_type={body.event_type}",
    )


@router.get(
    "/dlq",
    response_model=DlqListResponse,
    summary="List dead-letter queue items",
    description="Returns all webhook deliveries that have exhausted retry attempts.",
)
async def list_dlq() -> DlqListResponse:
    """GET /webhooks/dlq — list DLQ items."""
    dispatcher = _get_dispatcher()
    raw_items = dispatcher.list_dlq()
    items = [
        DlqItem(
            id=item["id"],
            event_id=item["event_id"],
            event_type=item["event_type"],
            target_url=item["target_url"],
            attempts=item["attempts"],
            last_error=item.get("last_error"),
            created_at=item["created_at"],
            updated_at=item["updated_at"],
        )
        for item in raw_items
    ]
    return DlqListResponse(items=items, count=len(items))


@router.post(
    "/dlq/{outbox_id}/replay",
    response_model=DlqReplayResponse,
    summary="Replay a dead-letter webhook",
    description="Reset a dead webhook delivery to pending so the worker will retry it.",
)
async def replay_dlq_item(outbox_id: str) -> DlqReplayResponse:
    """POST /webhooks/dlq/{outbox_id}/replay — replay a DLQ item."""
    dispatcher = _get_dispatcher()
    replayed = dispatcher.replay_dlq(outbox_id)

    if not replayed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DLQ item '{outbox_id}' not found or not in dead status",
        )

    return DlqReplayResponse(
        replayed=True,
        id=outbox_id,
        detail="Webhook reset to pending for re-delivery",
    )

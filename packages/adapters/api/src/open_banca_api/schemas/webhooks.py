"""Schemas for webhook-related endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class WebhookTestRequest(BaseModel):
    """Request body for POST /webhooks/test."""

    model_config = ConfigDict(extra="forbid")

    event_type: str


class DlqItem(BaseModel):
    """A dead-letter queue webhook delivery record."""

    model_config = ConfigDict(extra="allow")

    id: str
    event_id: str
    event_type: str
    target_url: str
    attempts: int
    last_error: str | None = None
    created_at: str
    updated_at: str


class DlqListResponse(BaseModel):
    """Response body for GET /webhooks/dlq."""

    items: list[DlqItem]
    count: int


class DlqReplayResponse(BaseModel):
    """Response body for POST /webhooks/dlq/{id}/replay."""

    replayed: bool
    id: str
    detail: str


class WebhookTestResponse(BaseModel):
    """Response body for POST /webhooks/test."""

    enqueued: bool
    outbox_id: str | None = None
    detail: str

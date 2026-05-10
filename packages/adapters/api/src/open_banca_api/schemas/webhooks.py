"""Schemas for webhook-related endpoints."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class WebhookTestRequest(BaseModel):
    """Request body for POST /webhooks/test."""

    model_config = ConfigDict(extra="forbid")

    event_type: str

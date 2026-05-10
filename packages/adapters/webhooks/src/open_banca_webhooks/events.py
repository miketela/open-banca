"""Canonical webhook event types per ADR-0011 / REQ-010."""

from __future__ import annotations

from enum import StrEnum


class WebhookEventType(StrEnum):
    """The 7 webhook event types."""

    JOB_CREATED = "job.created"
    JOB_OTP_REQUIRED = "job.otp_required"
    JOB_PROGRESS = "job.progress"
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"
    JOB_REMAP_PROPOSED = "job.remap_proposed"
    JOB_HUMAN_REQUIRED = "job.human_required"


# Mapping from domain WebhookEventType to this package's type (both use same string values)
_ALL_EVENT_TYPES: list[str] = [e.value for e in WebhookEventType]

__all__ = ["WebhookEventType", "_ALL_EVENT_TYPES"]

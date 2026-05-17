"""EventBusPort — outbound webhook event dispatch."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from open_banca_domain.entities.webhook_event import WebhookEvent


@runtime_checkable
class EventBusPort(Protocol):
    """Publishes domain events to the webhook outbox."""

    def publish(self, event: WebhookEvent) -> None: ...

    def retry_pending(self) -> None: ...

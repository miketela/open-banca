"""StartScrapeJob use case — initiates a scrape workflow."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from open_banca_domain.ports.event_bus_port import EventBusPort
from open_banca_domain.ports.job_store_port import JobStorePort
from open_banca_domain.ports.orchestrator_port import OrchestratorPort
from pydantic import BaseModel


class StartScrapeJobInput(BaseModel):
    bank: str
    credential_ref: str
    mode: str  # "full" | "incremental" — validated downstream by orchestrator

    def model_post_init(self, __context: object) -> None:
        if self.mode not in ("full", "incremental"):
            raise ValueError(f"mode must be 'full' or 'incremental', got '{self.mode}'")


@dataclass
class StartScrapeJobOutput:
    job_id: str


class StartScrapeJob:
    """Starts a scrape job via the orchestrator and emits job.created event."""

    def __init__(
        self,
        orchestrator: OrchestratorPort,
        job_store: JobStorePort,
        event_bus: EventBusPort,
    ) -> None:
        self._orchestrator = orchestrator
        self._job_store = job_store
        self._event_bus = event_bus

    def execute(self, input: StartScrapeJobInput) -> StartScrapeJobOutput:
        job_id = self._orchestrator.start_job(
            bank=input.bank,
            credential_ref=input.credential_ref,
            mode=input.mode,
        )
        # Emit job.created event — minimal payload, no sensitive data
        from open_banca_domain.entities.webhook_event import WebhookEvent, WebhookEventType

        event = WebhookEvent(
            id=str(uuid4()),
            event_type=WebhookEventType.JOB_CREATED,
            payload={"job_id": job_id, "bank": input.bank},
            signature="",  # Adapter fills real HMAC before delivery
            dispatched_at=datetime.now(UTC),
            job_id=job_id,
        )
        self._event_bus.publish(event)
        return StartScrapeJobOutput(job_id=job_id)

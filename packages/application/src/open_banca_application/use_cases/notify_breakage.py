"""NotifyBreakage use case — persist breakage event and enqueue job.failed webhook."""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from open_banca_domain.entities.breakage_event import BreakageEvent
from open_banca_domain.ports.breakage_event_port import BreakageEventPort


class NotifyBreakageInput(BaseModel):
    """Input for the NotifyBreakage use case."""

    job_id: str
    event: BreakageEvent

    model_config = {"arbitrary_types_allowed": True}


@dataclass
class NotifyBreakageOutput:
    """Output from the NotifyBreakage use case."""

    job_id: str


class NotifyBreakage:
    """Persists a BreakageEvent to the audit log and enqueues a ``job.failed`` webhook.

    This use case coordinates the two side-effects atomically via the
    ``BreakageEventPort``.  HTTP dispatch is intentionally NOT performed here
    — that is Task #25 (webhook delivery worker).

    Args:
        breakage_port: Port implementation that writes audit_log + webhook_outbox.
    """

    def __init__(self, breakage_port: BreakageEventPort) -> None:
        self._port = breakage_port

    def execute(self, input: NotifyBreakageInput) -> NotifyBreakageOutput:
        """Notify about a scrape step failure.

        Args:
            input: Contains the job_id and the BreakageEvent domain entity.

        Returns:
            Output containing the job_id for caller confirmation.
        """
        self._port.notify(event=input.event, job_id=input.job_id)
        return NotifyBreakageOutput(job_id=input.job_id)

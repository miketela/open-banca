"""BreakageEventPort — persist breakage events and enqueue job.failed webhooks."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from open_banca_domain.entities.breakage_event import BreakageEvent


@runtime_checkable
class BreakageEventPort(Protocol):
    """Atomically persist a BreakageEvent and enqueue its job.failed webhook."""

    def notify(self, event: BreakageEvent, job_id: str) -> None: ...

"""OrchestratorPort — Temporal workflow lifecycle management."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class OrchestratorPort(Protocol):
    """Starts and signals Temporal workflows for scrape jobs."""

    def start_job(self, bank: str, credential_ref: str, mode: str) -> str: ...

    def signal_otp_confirmed(self, job_id: str) -> None: ...

    def signal_remap_approved(self, proposal_id: str) -> None: ...

    def cancel_job(self, job_id: str) -> None: ...

    def query_status(self, job_id: str) -> str: ...

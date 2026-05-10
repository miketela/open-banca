"""WorkflowEnginePort — Temporal-specific workflow engine seam."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from open_banca_domain.ports.orchestrator_port import OrchestratorPort


@runtime_checkable
class WorkflowEnginePort(OrchestratorPort, Protocol):
    """Extends OrchestratorPort with Temporal-specific helpers for test seams."""

    def get_workflow_history(self, job_id: str) -> list[dict[str, Any]]: ...

    def time_skip(self, seconds: float) -> None: ...

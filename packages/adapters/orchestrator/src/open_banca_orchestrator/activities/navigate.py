"""NavigateActivity — navigate to the account's transaction section in the bank portal.

Retry policy (orchestrator.md §Inventario):
  - Exponential backoff, 3 attempts.
  - start-to-close timeout: 30 s per step.
  - Heartbeat every 5 s on long steps.

Idempotency key: job_id + step_id.

IMPLEMENTATION STATUS: skeleton — raises NotImplementedError.
Real implementation lands in the task that wires up the Playwright Runner.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from temporalio import activity

from open_banca_orchestrator.activities.login import BrowserSessionToken


class NavigateInput(BaseModel):
    """Input for NavigateActivity."""

    job_id: str = Field(description="Unique job identifier")
    step_id: str = Field(
        description="Logical step identifier for idempotency (job_id + step suffix)"
    )
    bank_id: str = Field(description="Bank identifier to locate map.json step definitions")
    account_id: str = Field(description="Account identifier to navigate to")
    browser_session_token: BrowserSessionToken = Field(
        description="Active sidecar session token (ADR-0019)"
    )


class NavigateResult(BaseModel):
    """Result from NavigateActivity."""

    current_url: str = Field(description="Final URL after navigation")
    page_title: str = Field(description="Page title confirming navigation success")
    screenshot_path: str | None = Field(
        default=None,
        description="Path to screenshot inside sandbox container for debugging",
    )


class NavigateActivity:
    """NavigateActivity class-based wrapper."""


@activity.defn(name="NavigateActivity")
async def navigate(input: NavigateInput) -> NavigateResult:
    """Navigate to the account transaction page using map.json step definitions.

    Sends heartbeats every 5 s on long steps to prevent heartbeat timeout.

    TODO: load step definitions from map.json for bank_id.
    TODO: execute Playwright navigation steps via sidecar socket.
    TODO: call activity.heartbeat(step=step_name) on each navigation step.
    """
    raise NotImplementedError(
        "NavigateActivity not implemented — wired in task that implements Playwright Runner"
    )

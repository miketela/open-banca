"""OTPSignalAwaitActivity — long-running keepalive during OTP wait (ADR-0019).

This activity's sole purpose is to keep the BrowserSidecar connection alive via
Temporal heartbeats while the workflow waits for the otp_confirmed signal.

Retry policy (orchestrator.md §Inventario):
  - NO retry (sin retry).
  - start-to-close timeout: 4 min (hard cap matching the signal wait).
  - Heartbeat every 15 s to keep the sidecar alive and detect sidecar_unreachable.

The workflow waits for otp_confirmed using workflow.wait_condition() with a
timedelta(minutes=4) timeout SEPARATELY from this activity. Both coexist:
  - workflow.wait_condition: deterministic signal delivery.
  - OTPSignalAwaitActivity:  non-deterministic CDP keepalive via sidecar heartbeat.

ADR-0019 fault taxonomy:
  - sidecar_unreachable → ApplicationError(non_retryable=True, type="sidecar_unreachable")
  - browser_lost       → ApplicationError(non_retryable=True, type="browser_lost")
  - session_lost       → ApplicationError(non_retryable=True, type="session_lost")

IMPLEMENTATION STATUS: skeleton — raises NotImplementedError.
Real implementation lands in the task that wires up BrowserSidecar IPC.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from temporalio import activity

from open_banca_orchestrator.activities.login import BrowserSessionToken


class OTPSignalAwaitInput(BaseModel):
    """Input for OTPSignalAwaitActivity."""

    job_id: str = Field(description="Unique job identifier")
    browser_session_token: BrowserSessionToken = Field(
        description="Token from LoginActivity encoding sidecar socket path (ADR-0019)"
    )
    heartbeat_interval_s: int = Field(
        default=15,
        description="Seconds between heartbeat pings to the BrowserSidecar",
    )


class OTPSignalAwaitResult(BaseModel):
    """Result from OTPSignalAwaitActivity."""

    sidecar_alive: bool = Field(
        description="True if sidecar was still alive when activity completed"
    )
    heartbeat_count: int = Field(
        description="Number of successful heartbeat pings sent during the wait"
    )


class OTPSignalAwaitActivity:
    """OTPSignalAwaitActivity class-based wrapper."""


@activity.defn(name="OTPSignalAwaitActivity")
async def otp_signal_await(input: OTPSignalAwaitInput) -> OTPSignalAwaitResult:  # noqa: A002
    """Send Temporal heartbeats while keeping the BrowserSidecar CDP connection alive.

    Loop: ping sidecar via Unix socket → report heartbeat to Temporal → sleep
    heartbeat_interval_s. Exit when activity is cancelled (otp_confirmed arrived)
    or start-to-close timeout fires (4 min hard cap).

    On sidecar failure: raise ApplicationError(non_retryable=True).

    TODO: implement sidecar IPC ping via Unix socket (ADR-0019).
    TODO: raise ApplicationError with appropriate fault type on connection loss.
    TODO: call activity.heartbeat(sidecar_alive=True, ping_count=N) each iteration.
    """
    raise NotImplementedError(
        "OTPSignalAwaitActivity not implemented — requires BrowserSidecar IPC (ADR-0019)"
    )

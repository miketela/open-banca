"""LoginActivity — authenticate to a bank portal and detect OTP requirement.

Retry policy (orchestrator.md §Inventario):
  - Exponential backoff, 2 attempts max.
  - start-to-close timeout: 90 s.
  - Heartbeat every 10 s while awaiting login page response.

Idempotency key: hash(credential_ref + nonce) so replays don't re-submit form.

If the bank requires OTP (Clave Móvil), the activity spawns the BrowserSidecar
(ADR-0019) to keep the CDP connection alive across a potential worker crash, then
returns LoginStatus.needs_otp together with a browser_session_token encoding the
sidecar socket path.

IMPLEMENTATION STATUS: skeleton — raises NotImplementedError.
Real implementation lands in the task that wires up the Playwright Runner.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field
from temporalio import activity


class LoginStatus(StrEnum):
    """Outcome of a login attempt."""

    success = "success"
    needs_otp = "needs_otp"
    failed = "failed"


class BrowserSessionToken(BaseModel):
    """Opaque token that identifies the BrowserSidecar connection (ADR-0019).

    Fields:
        container_id: Docker container ID of the sandbox for lifecycle checks.
        socket_path:  Unix socket path of the BrowserSidecar process inside the container.
        sidecar_pid:  PID of the sidecar process for liveness verification on reconnect.
    """

    container_id: str = Field(description="Docker container ID of the sandbox")
    socket_path: str = Field(
        default="/run/banca/sidecar.sock",
        description="Unix socket path for sidecar IPC",
    )
    sidecar_pid: int = Field(description="PID of the BrowserSidecar process")


class LoginInput(BaseModel):
    """Input for LoginActivity."""

    job_id: str = Field(description="Unique job identifier (idempotency key component)")
    bank_id: str = Field(description="Bank identifier — used to locate map.json")
    credential_ref: str = Field(
        description="Reference to encrypted credential in secrets store (never plaintext)"
    )
    nonce: str = Field(
        description="Per-attempt nonce for idempotency; workflow generates via workflow.uuid4()"
    )
    sandbox_container_id: str = Field(description="Container ID returned by sandbox spawn step")


class LoginResult(BaseModel):
    """Result from LoginActivity."""

    status: LoginStatus
    browser_session_token: BrowserSessionToken | None = Field(
        default=None,
        description="Set when status==needs_otp; null when status==success",
    )
    error_detail: str | None = Field(
        default=None,
        description="Human-readable error detail when status==failed",
    )


class LoginActivity:
    """LoginActivity class-based wrapper (no-op; activity is a module-level function)."""


@activity.defn(name="LoginActivity")
async def login(input: LoginInput) -> LoginResult:
    """Authenticate to the bank portal.

    Sends heartbeats every 10 s while waiting for the login page to respond.
    If OTP is required, spawns BrowserSidecar (ADR-0019) before returning.

    TODO: implement real Playwright login via map.json steps.
    TODO: spawn BrowserSidecar when login returns needs_otp.
    TODO: integrate with credential store to decrypt credential_ref.
    """
    raise NotImplementedError(
        "LoginActivity not implemented — wired in task that implements Playwright Runner"
    )

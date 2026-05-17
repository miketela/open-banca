"""ConfirmOTP use case — signals otp_confirmed to the running workflow."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from open_banca_domain.ports.orchestrator_port import OrchestratorPort


class ConfirmOTPInput(BaseModel):
    job_id: str


@dataclass
class ConfirmOTPOutput:
    success: bool


class ConfirmOTP:
    """Sends otp_confirmed signal to the Temporal workflow."""

    def __init__(self, orchestrator: OrchestratorPort) -> None:
        self._orchestrator = orchestrator

    def execute(self, input: ConfirmOTPInput) -> ConfirmOTPOutput:
        self._orchestrator.signal_otp_confirmed(input.job_id)
        return ConfirmOTPOutput(success=True)

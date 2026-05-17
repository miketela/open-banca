"""Human-input wait bridge — polls storage until operator delivers an answer."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from open_banca_browser.errors import HumanInputRequired


class HumanInputTimeoutError(Exception):
    """Raised when no human input arrives before timeout_s expires."""


class HumanInputPollPort(Protocol):
    """Storage port used by the runner pause loop."""

    def save_human_input_answer(
        self, job_id: str, field_key: str, answer: str, *, persist: bool
    ) -> None: ...

    def take_human_input_answer(
        self, job_id: str, field_key: str
    ) -> tuple[str, bool] | None: ...


OnHumanInputRequired = Callable[[HumanInputRequired], None]


@dataclass(frozen=True)
class PollingHumanInputWaiter:
    """Polls ``take_human_input_answer`` until an answer is available."""

    job_id: str
    poll_port: HumanInputPollPort
    poll_interval_s: float = 2.0
    sleep_fn: Callable[[float], None] | None = None
    heartbeat_fn: Callable[[], None] | None = None
    on_required: OnHumanInputRequired | None = None

    def wait_for_answer(self, required: HumanInputRequired) -> tuple[str, bool]:
        """Block until the operator answer is stored, then return (answer, persist)."""
        import time

        sleep = self.sleep_fn or time.sleep
        if self.on_required is not None:
            self.on_required(required)

        deadline = time.monotonic() + required.timeout_s
        while time.monotonic() < deadline:
            row = self.poll_port.take_human_input_answer(self.job_id, required.field_key)
            if row is not None:
                return row
            if self.heartbeat_fn is not None:
                self.heartbeat_fn()
            sleep(self.poll_interval_s)

        raise HumanInputTimeoutError(
            f"Human input not received for field_key={required.field_key!r} "
            f"within {required.timeout_s}s"
        )

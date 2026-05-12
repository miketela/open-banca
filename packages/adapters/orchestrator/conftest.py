"""Pytest configuration for open_banca_orchestrator tests.

Patches Temporal's Worker to inject a beartype-passthrough SandboxedWorkflowRunner
when no custom workflow_runner is specified. This is required because pydantic-ai
(loaded by LLM adapter tests in the same pytest session) installs beartype import
hooks globally. Without this passthrough, Temporal's sandbox triggers a beartype
circular import error when the Worker is initialized after pydantic-ai has loaded.

The Worker accepts a `workflow_runner` parameter. We patch `Worker.__init__`
to substitute a beartype-safe runner when the caller uses the default (None).

References:
  - https://python.temporal.io/temporalio.worker.Worker.html#workflow_runner
  - beartype issue: import hooks installed via sys.meta_path affect sandbox copy
"""

from __future__ import annotations

from temporalio.worker import Replayer, Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

# Build the beartype-safe runner at import time (before any test runs).
_BEARTYPE_RESTRICTIONS: SandboxRestrictions = (
    SandboxRestrictions.default.with_passthrough_modules("beartype")
)
_BEARTYPE_RUNNER: SandboxedWorkflowRunner = SandboxedWorkflowRunner(
    restrictions=_BEARTYPE_RESTRICTIONS
)


def pytest_configure(config: object) -> None:  # noqa: ARG001
    """Patch Worker.__init__ to use beartype-passthrough sandbox runner.

    Called before test collection. Ensures all Workers created in this pytest
    session use restrictions that include beartype in passthrough_modules.
    """
    _patch_worker()


def _patch_worker() -> None:
    """Monkey-patch Worker and Replayer to inject _BEARTYPE_RUNNER as default."""
    _patch_class(Worker)
    _patch_class(Replayer)


def _patch_class(cls: type) -> None:
    """Patch cls.__init__ to substitute _BEARTYPE_RUNNER when workflow_runner is None."""
    original_init = cls.__init__

    def _patched_init(
        self: object,
        *args: object,
        workflow_runner: object = None,
        **kwargs: object,
    ) -> None:
        if workflow_runner is None:
            workflow_runner = _BEARTYPE_RUNNER
        original_init(self, *args, workflow_runner=workflow_runner, **kwargs)  # type: ignore[arg-type]

    cls.__init__ = _patched_init  # type: ignore[method-assign]

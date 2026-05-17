"""Shared helpers for step executors."""

from __future__ import annotations

from typing import Any

from open_banca_domain.entities.bank_map import StepSpec


def extra(step: StepSpec) -> dict[str, Any]:
    """Return step.model_extra as a non-None dict.

    Pydantic's model_extra is typed as dict | None even when extra='allow'.
    This helper provides a safe, typed accessor.
    """
    return step.model_extra or {}

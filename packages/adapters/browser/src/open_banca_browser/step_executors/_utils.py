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


def root_locator(page: Any, step: StepSpec) -> Any:
    """Return page or a FrameLocator when ``frame_selector`` is set on the step."""
    params = extra(step)
    frame_selector: str = params.get("frame_selector") or params.get("iframe_selector") or ""
    if frame_selector:
        return page.frame_locator(frame_selector)
    return page

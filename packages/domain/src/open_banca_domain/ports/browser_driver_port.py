"""BrowserDriverPort — Playwright browser session abstraction."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BrowserDriverPort(Protocol):
    """Low-level browser automation without LLM logic."""

    def launch_session(self, job_id: str) -> str: ...

    def navigate(self, session_id: str, url: str) -> None: ...

    def fill_sensitive(self, session_id: str, selector: str, value: str) -> None: ...

    def click(self, session_id: str, selector: str) -> None: ...

    def screenshot(self, session_id: str) -> bytes: ...

    def close(self, session_id: str) -> None: ...

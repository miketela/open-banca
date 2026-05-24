"""ClockPort — deterministic time abstraction for Temporal determinism."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class ClockPort(Protocol):
    """Returns current UTC time. Fake impl enables deterministic testing."""

    def now(self) -> datetime: ...

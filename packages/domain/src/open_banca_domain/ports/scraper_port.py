"""ScraperPort — executes a BankMap and emits BreakageEvents."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from open_banca_domain.entities.bank_map import BankMap
from open_banca_domain.entities.breakage_event import BreakageEvent
from open_banca_domain.entities.credential import Credential


@dataclass
class ScrapeResult:
    """Raw output from a scrape execution."""

    raw_data: bytes
    breakage_events: list[BreakageEvent]
    metadata: dict[str, Any]


@runtime_checkable
class ScraperPort(Protocol):
    """Pure Playwright runner — no LLM, no decision logic."""

    def execute_map(self, map: BankMap, credential: Credential) -> ScrapeResult: ...

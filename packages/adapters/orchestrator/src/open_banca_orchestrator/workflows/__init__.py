"""Temporal workflow definitions for open-banca.

Workflows:
  ScrapeJobWorkflow  — root workflow, one per POST /scrape request.
  MapBankWorkflow    — child workflow that runs the Mapper agent (task 14).
  RemapBankWorkflow  — child workflow that runs the Remapper agent (task 20).

DETERMINISM RULES — all code in this package MUST follow:
  1. Use workflow.now()     — NOT datetime.now() or time.time()
  2. Use workflow.random()  — NOT random.random() or secrets.token_*
  3. Use workflow.uuid4()   — NOT uuid.uuid4()
  4. All I/O via activities — NO network, file, or DB calls in workflow code
  5. No threading, no locks, no mutable module-level state
"""

from open_banca_orchestrator.workflows.map_bank import MapBankWorkflow
from open_banca_orchestrator.workflows.remap_bank import RemapBankWorkflow
from open_banca_orchestrator.workflows.scrape_job import (
    ScrapeJobInput,
    ScrapeJobResult,
    ScrapeJobWorkflow,
)

__all__ = [
    "ScrapeJobWorkflow",
    "ScrapeJobInput",
    "ScrapeJobResult",
    "MapBankWorkflow",
    "RemapBankWorkflow",
]

"""ScraperRunner — Playwright-based implementation of ScraperPort.

Zero LLM calls. Pure Playwright sync API.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Callable
from typing import Any

from open_banca_browser.breakage import build_breakage_event
from open_banca_browser.errors import ScraperError
from open_banca_browser.screenshot import capture_screenshot
from open_banca_browser.step_dispatcher import dispatch_step
from open_banca_browser.step_executors._utils import extra
from open_banca_domain.entities.bank_map import BankMap
from open_banca_domain.entities.breakage_event import BreakageEvent
from open_banca_domain.entities.credential import Credential
from open_banca_domain.ports.scraper_port import ScrapeResult

logger = logging.getLogger(__name__)

_REAL_BROWSER = os.environ.get("OPEN_BANCA_PLAYWRIGHT_REAL", "").strip() == "1"


def _default_job_id_provider() -> str:
    return str(uuid.uuid4())


def _raise_on_unresolved(value_ref: str) -> str:
    raise RuntimeError(
        f"No secret_resolver provided; cannot resolve value_ref={value_ref!r}"
    )


class ScraperRunner:
    """Executes a BankMap using Playwright sync API.

    This class satisfies ScraperPort via structural (Protocol) typing.

    Args:
        job_id_provider: Callable that returns a unique job ID per execution.
                         Defaults to uuid4().
        secret_resolver: Callable mapping value_ref → plaintext secret.
                         Required for maps with 'fill' steps.
        headless: Run Chromium in headless mode. Defaults to True.
    """

    def __init__(
        self,
        *,
        job_id_provider: Callable[[], str] | None = None,
        secret_resolver: Callable[[str], str] | None = None,
        headless: bool = True,
    ) -> None:
        self._job_id_provider = job_id_provider or _default_job_id_provider
        self._secret_resolver: Callable[[str], str] = (
            secret_resolver or _raise_on_unresolved
        )
        self._headless = headless

    def execute_map(self, map: BankMap, credential: Credential) -> ScrapeResult:
        """Execute all steps in the BankMap sequentially.

        Args:
            map: The versioned BankMap to execute.
            credential: Credential reference (used to identify the job context).

        Returns:
            ScrapeResult with raw_data, breakage_events, and metadata.
        """
        job_id = self._job_id_provider()
        logger.info("ScraperRunner starting job=%s bank=%s", job_id, map.bank_id)

        if not _REAL_BROWSER:
            return self._execute_stub(map, credential, job_id)

        from playwright.sync_api import sync_playwright  # lazy — only in real mode

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self._headless)
            context = browser.new_context()
            page = context.new_page()
            try:
                return self._run_steps(page, map, credential, job_id)
            finally:
                context.close()
                browser.close()

    def _execute_stub(
        self, map: BankMap, credential: Credential, job_id: str
    ) -> ScrapeResult:
        """Return empty result without launching a browser (CI / unit test mode)."""
        logger.debug(
            "ScraperRunner stub mode (OPEN_BANCA_PLAYWRIGHT_REAL not set) "
            "job=%s bank=%s",
            job_id,
            map.bank_id,
        )
        return ScrapeResult(
            raw_data=b"",
            breakage_events=[],
            metadata={
                "job_id": job_id,
                "bank_id": map.bank_id,
                "map_version": map.version,
                "stub": True,
            },
        )

    def _run_steps(
        self,
        page: Any,
        map: BankMap,
        credential: Credential,
        job_id: str,
    ) -> ScrapeResult:
        """Core step execution loop."""
        breakage_events: list[BreakageEvent] = []
        download_store: list[bytes] = []
        extracted_rows: list[dict[str, str]] = []

        for step_index, step in enumerate(map.steps):
            logger.debug(
                "job=%s step=%d action=%s", job_id, step_index, step.action
            )
            try:
                dispatch_step(
                    page,
                    step,
                    secret_resolver=self._secret_resolver,
                    download_store=download_store,
                    extracted_rows=extracted_rows,
                )
            except ScraperError as exc:
                logger.warning(
                    "job=%s step=%d action=%s FAILED: %s",
                    job_id,
                    step_index,
                    step.action,
                    exc,
                )
                png = _safe_screenshot(page)
                params = extra(step)
                selector = params.get("selector") or params.get("table_selector")
                event = build_breakage_event(
                    job_id=job_id,
                    step_index=step_index,
                    step_type=step.action,
                    error=exc,
                    screenshot_png=png,
                    page=page,
                    selector=selector,
                )
                breakage_events.append(event)
                # Stop executing further steps after a breakage
                break
            except Exception as exc:
                logger.error(
                    "job=%s step=%d unexpected error: %s", job_id, step_index, exc
                )
                png = _safe_screenshot(page)
                event = build_breakage_event(
                    job_id=job_id,
                    step_index=step_index,
                    step_type=step.action,
                    error=exc,
                    screenshot_png=png,
                    page=page,
                )
                breakage_events.append(event)
                break

        # Determine raw_data: prefer download payload, else JSON rows
        raw_data: bytes
        if download_store:
            raw_data = download_store[-1]
        elif extracted_rows:
            raw_data = json.dumps(extracted_rows).encode()
        else:
            raw_data = b""

        return ScrapeResult(
            raw_data=raw_data,
            breakage_events=breakage_events,
            metadata={
                "job_id": job_id,
                "bank_id": map.bank_id,
                "map_version": map.version,
                "steps_total": len(map.steps),
                "steps_completed": (
                    len(map.steps) if not breakage_events
                    else breakage_events[0].step_index
                ),
                "downloads": len(download_store),
                "extracted_rows": len(extracted_rows),
            },
        )


def _safe_screenshot(page: Any) -> bytes:
    """Capture screenshot, returning empty bytes on failure."""
    try:
        return capture_screenshot(page)
    except Exception:
        return b""

"""HU08 tests: sandbox CDP routing, map cosign verification at runtime."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from temporalio.exceptions import ApplicationError

from open_banca_orchestrator.activities.execute_scrape_map import (
    ExecuteScrapeMapInput,
    _load_bank_map,
    _resolve_sandbox_cdp_url,
    _sandbox_skip_active,
)


def test_resolve_sandbox_cdp_url_from_container_ip() -> None:
    url = _resolve_sandbox_cdp_url("abc123", "172.20.0.5")
    assert url == "http://172.20.0.5:9222"


def test_resolve_sandbox_cdp_url_skip_local_dev() -> None:
    assert _resolve_sandbox_cdp_url("local-skip-job001", None) is None


def test_resolve_sandbox_cdp_url_explicit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "OPEN_BANCA_SANDBOX_CDP_URL",
        "ws://sandbox-{container_id}.internal:{container_ip}",
    )
    url = _resolve_sandbox_cdp_url("cid-99", "10.0.0.2")
    assert url == "ws://sandbox-cid-99.internal:10.0.0.2"


def test_sandbox_skip_active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_BANCA_SKIP_SANDBOX", "1")
    assert _sandbox_skip_active() is True
    monkeypatch.setenv("OPEN_BANCA_SKIP_SANDBOX", "0")
    assert _sandbox_skip_active() is False


def test_load_bank_map_unsigned_rejected_when_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_BANCA_REQUIRE_SIGNED_MAPS", "1")
    bank_dir = (
        __import__("pathlib").Path(__file__).resolve().parents[4]
        / "packages"
        / "banks"
        / "banco_general"
    )
    if not (bank_dir / "map.json").exists():
        pytest.skip("banco_general map.json not present")

    with patch(
        "open_banca_parsing.community.verifier._verify_cosign",
        return_value=(False, None, None, "unsigned"),
    ):
        with pytest.raises(ApplicationError) as exc_info:
            _load_bank_map("banco_general")
    assert exc_info.value.type == "unsigned_map"


def test_execute_scrape_map_input_passes_sandbox_fields() -> None:
    inp = ExecuteScrapeMapInput(
        job_id="j1",
        bank_id="banco_general",
        credential_ref="ref",
        sandbox_container_id="cid",
        sandbox_container_ip="172.20.0.2",
    )
    assert inp.sandbox_container_ip == "172.20.0.2"

"""Tests for ScraperRunner — integration-style tests using stub mode (no Chromium)."""
from __future__ import annotations

import pytest

from open_banca_browser.runner import ScraperRunner
from open_banca_domain.entities.bank_map import BankMap, StepSpec
from open_banca_domain.entities.credential import Credential


@pytest.fixture()
def simple_credential() -> Credential:
    return Credential(
        id="cred-001",
        bank="banco-general",
        credential_ref="vault://banco-general/user",
        label="test credential",
    )


@pytest.fixture()
def simple_map() -> BankMap:
    return BankMap(
        bank_id="banco-general",
        version="1.0.0",
        schema_version="1",
        steps=[
            StepSpec(
                step_id="s1",
                action="navigate",
                target="https://bancogeneral.com",
                url="https://bancogeneral.com",
                wait_until="load",
            ),
            StepSpec(
                step_id="s2",
                action="click",
                target="#login-btn",
                selector="#login-btn",
            ),
        ],
    )


class TestScraperRunnerStubMode:
    """Tests that run without a real browser (OPEN_BANCA_PLAYWRIGHT_REAL not set)."""

    def test_returns_scrape_result(
        self, simple_map: BankMap, simple_credential: Credential
    ) -> None:
        runner = ScraperRunner()
        result = runner.execute_map(simple_map, simple_credential)
        assert result is not None
        assert result.raw_data == b""
        assert result.breakage_events == []

    def test_metadata_contains_job_info(
        self, simple_map: BankMap, simple_credential: Credential
    ) -> None:
        runner = ScraperRunner()
        result = runner.execute_map(simple_map, simple_credential)
        assert result.metadata["bank_id"] == "banco-general"
        assert result.metadata["map_version"] == "1.0.0"
        assert "job_id" in result.metadata

    def test_custom_job_id_provider(
        self, simple_map: BankMap, simple_credential: Credential
    ) -> None:
        runner = ScraperRunner(job_id_provider=lambda: "fixed-job-42")
        result = runner.execute_map(simple_map, simple_credential)
        assert result.metadata["job_id"] == "fixed-job-42"

    def test_stub_flag_in_metadata(
        self, simple_map: BankMap, simple_credential: Credential
    ) -> None:
        runner = ScraperRunner()
        result = runner.execute_map(simple_map, simple_credential)
        assert result.metadata.get("stub") is True

    def test_satisfies_scraper_port_protocol(
        self, simple_map: BankMap, simple_credential: Credential
    ) -> None:
        """ScraperRunner satisfies ScraperPort via Protocol structural typing."""
        from open_banca_domain.ports.scraper_port import ScraperPort

        runner = ScraperRunner()
        assert isinstance(runner, ScraperPort)

    def test_multiple_executions_unique_job_ids(
        self, simple_map: BankMap, simple_credential: Credential
    ) -> None:
        runner = ScraperRunner()
        r1 = runner.execute_map(simple_map, simple_credential)
        r2 = runner.execute_map(simple_map, simple_credential)
        assert r1.metadata["job_id"] != r2.metadata["job_id"]

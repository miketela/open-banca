"""Tests for ScraperRunner — integration-style tests using stub mode (no Chromium)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from open_banca_browser.errors import HumanInputRequired, ScraperError
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


class TestScraperRunnerHumanInputPause:
    """Human-input pause must not create breakage events."""

    @pytest.fixture()
    def human_input_map(self) -> BankMap:
        return BankMap(
            bank_id="banco-general",
            version="1.0.0",
            schema_version="1",
            steps=[
                StepSpec(
                    step_id="pause",
                    action="prompt_user",
                    target="#q",
                    question_selector="#q",
                    field_key="security_q_1",
                    selector="#answer",
                ),
                StepSpec(
                    step_id="after",
                    action="click",
                    target="#next",
                    selector="#next",
                ),
            ],
        )

    @pytest.fixture()
    def human_input_required(self) -> HumanInputRequired:
        return HumanInputRequired(
            question_text="What is your pet's name?",
            field_key="security_q_1",
            question_hash="deadbeef" * 8,
            selector="#answer",
            timeout_s=60,
        )

    def test_run_steps_waits_fills_and_continues(
        self,
        human_input_map: BankMap,
        simple_credential: Credential,
        human_input_required: HumanInputRequired,
    ) -> None:
        page = MagicMock()
        waiter = MagicMock()
        waiter.wait_for_answer.return_value = ("fluffy", True)
        vault = MagicMock()

        def dispatch_side_effect(page_arg, step, **kwargs):
            assert kwargs["vault"] is vault
            assert kwargs["bank_id"] == "banco-general"
            assert kwargs["credential_id"] == "cred-001"
            if step.step_id == "pause":
                raise human_input_required

        runner = ScraperRunner(
            human_input_waiter=waiter,
            vault=vault,
            credential_id="cred-001",
        )

        with (
            patch(
                "open_banca_browser.runner.dispatch_step",
                side_effect=dispatch_side_effect,
            ) as mock_dispatch,
            patch("open_banca_browser.runner.fill_prompt_user_answer") as mock_fill,
        ):
            result = runner._run_steps(
                page, human_input_map, simple_credential, "job-hi"
            )

        waiter.wait_for_answer.assert_called_once_with(human_input_required)
        vault.store_security_answer.assert_called_once_with(
            "cred-001",
            human_input_required.question_hash,
            "fluffy",
            field_key="security_q_1",
        )
        mock_fill.assert_called_once_with(page, "#answer", "fluffy")
        assert mock_dispatch.call_count == 2
        assert result.breakage_events == []
        assert result.metadata["steps_completed"] == 2

    def test_human_input_without_waiter_propagates(
        self,
        human_input_map: BankMap,
        simple_credential: Credential,
        human_input_required: HumanInputRequired,
    ) -> None:
        runner = ScraperRunner()

        with patch(
            "open_banca_browser.runner.dispatch_step",
            side_effect=human_input_required,
        ):
            with pytest.raises(HumanInputRequired):
                runner._run_steps(
                    MagicMock(), human_input_map, simple_credential, "job-x"
                )

    def test_human_input_not_scraper_error(
        self, human_input_required: HumanInputRequired
    ) -> None:
        assert not isinstance(human_input_required, ScraperError)

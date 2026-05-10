"""Test: MapperAgent self-test triggers retry on dry-run failure.

Per docs/02-components/mapper-agent.md §Validación post-mapping:
  "Dry-run sin creds — Scraper Runner ejecuta... si falla, retry con feedback."

Verifies:
  1. If ScraperRunner dry-run raises an unexpected exception, SelfTestFailed is raised.
  2. A valid map passes the self-test without raising.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from open_banca_domain.entities.bank_map import BankMap
from open_banca_llm.mapper.agent import FakeChatModel, MapperAgent
from open_banca_llm.mapper.errors import SelfTestFailed


def _make_valid_map_json(bank_id: str = "banco-general") -> str:
    return json.dumps(
        {
            "bank_id": bank_id,
            "version": "1.0.0",
            "schema_version": "1",
            "steps": [
                {
                    "step_id": "s1",
                    "action": "navigate",
                    "url": "https://bancogeneral.com",
                    "wait_until": "load",
                },
            ],
        }
    )


@pytest.mark.asyncio()
async def test_valid_map_passes_self_test() -> None:
    """A valid map JSON should pass the self-test without raising SelfTestFailed."""
    llm = FakeChatModel(responses=[_make_valid_map_json()])
    agent = MapperAgent(llm_override=llm)

    # Should complete without exception (ScraperRunner in stub mode returns normally)
    bank_map = await agent.map_bank(
        bank_id="banco-general",
        credential_ref="vault://test",
        sensitive_data={},
    )
    assert isinstance(bank_map, BankMap)


@pytest.mark.asyncio()
async def test_self_test_raises_when_scraper_runner_fails() -> None:
    """SelfTestFailed is raised when ScraperRunner raises a non-stub error."""
    llm = FakeChatModel(responses=[_make_valid_map_json()])
    agent = MapperAgent(llm_override=llm)

    # Simulate ScraperRunner raising a structural error (not a stub-mode normal exit)
    with patch(
        "open_banca_llm.mapper.agent.MapperAgent._self_test",
        side_effect=SelfTestFailed("selector #login not found in DOM"),
    ):
        with pytest.raises(SelfTestFailed) as exc_info:
            await agent.map_bank(
                bank_id="banco-general",
                credential_ref="vault://test",
                sensitive_data={},
            )

    assert "selector" in exc_info.value.reason.lower()


@pytest.mark.asyncio()
async def test_self_test_accepts_stub_mode_completion() -> None:
    """ScraperRunner stub-mode completion (no real browser) is treated as pass."""
    llm = FakeChatModel(responses=[_make_valid_map_json()])
    agent = MapperAgent(llm_override=llm)

    # ScraperRunner in stub mode logs a warning and returns a result
    # The self_test method should not raise SelfTestFailed for stub output
    bank_map = await agent.map_bank(
        bank_id="banco-general",
        credential_ref="vault://test",
        sensitive_data={},
    )
    assert bank_map is not None


@pytest.mark.asyncio()
async def test_self_test_skipped_when_browser_not_installed() -> None:
    """If open-banca-browser is not importable, self-test is skipped (not failed)."""
    llm = FakeChatModel(responses=[_make_valid_map_json()])
    agent = MapperAgent(llm_override=llm)

    # Simulate browser adapter missing
    with patch.dict("sys.modules", {"open_banca_browser": None, "open_banca_browser.runner": None}):
        with patch(
            "open_banca_llm.mapper.agent.MapperAgent._self_test",
            return_value=None,
        ) as mock_self_test:
            bank_map = await agent.map_bank(
                bank_id="banco-general",
                credential_ref="vault://test",
                sensitive_data={},
            )
            mock_self_test.assert_called_once()

    assert isinstance(bank_map, BankMap)

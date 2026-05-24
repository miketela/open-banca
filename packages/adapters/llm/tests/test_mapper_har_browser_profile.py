"""MapperAgent — Browser HAR profile passed to browser-use Agent when har_output_path set."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from open_banca_llm.mapper.agent import MapperAgent
from open_banca_llm.mapper.cost_tracker import CostTracker


@pytest.mark.asyncio()
async def test_run_with_browser_passes_browser_profile_when_har_path_set(tmp_path) -> None:
    """When har_output_path is set, Agent receives BrowserProfile with record_har_* kwargs."""
    har_path = tmp_path / "mapper_run.har"
    agent = MapperAgent(har_output_path=har_path)
    tracker = CostTracker(cost_cap_usd=99.0, wallclock_cap_seconds=99999.0)
    llm = MagicMock()

    mock_history = MagicMock()
    mock_history.final_result.return_value = json.dumps(
        {
            "bank_id": "banco_general",
            "version": "1.0.0",
            "schema_version": "1",
            "steps": [
                {
                    "step_id": "s1",
                    "action": "navigate",
                    "target": "https://example.com",
                    "url": "https://example.com",
                },
            ],
        }
    )

    with patch("browser_use.Agent") as MockAgent:
        inst = MagicMock()
        inst.run = AsyncMock(return_value=mock_history)
        MockAgent.return_value = inst

        await agent._run_with_browser(llm, "do the thing", {"u": "p"}, tracker)

    call_kw = MockAgent.call_args.kwargs
    profile = call_kw.get("browser_profile")
    assert profile is not None
    assert str(profile.record_har_path) == str(har_path)
    assert profile.record_har_content == "embed"


@pytest.mark.asyncio()
async def test_run_with_browser_omits_browser_profile_when_no_har_path(tmp_path) -> None:
    """When har_output_path is None, browser_profile is not passed (defaults to Agent)."""
    agent = MapperAgent(har_output_path=None)
    tracker = CostTracker(cost_cap_usd=99.0, wallclock_cap_seconds=99999.0)
    llm = MagicMock()

    mock_history = MagicMock()
    mock_history.final_result.return_value = json.dumps(
        {
            "bank_id": "banco_general",
            "version": "1.0.0",
            "schema_version": "1",
            "steps": [
                {
                    "step_id": "s1",
                    "action": "navigate",
                    "target": "https://example.com",
                    "url": "https://example.com",
                },
            ],
        }
    )

    with patch("browser_use.Agent") as MockAgent:
        inst = MagicMock()
        inst.run = AsyncMock(return_value=mock_history)
        MockAgent.return_value = inst

        await agent._run_with_browser(llm, "task", {}, tracker)

    call_kw = MockAgent.call_args.kwargs
    assert call_kw.get("browser_profile") is None

"""Test: MapperAgent produces a valid BankMap using FakeChatModel (zero real API calls).

'PydanticAI test models' in CLAUDE.md = FakeChatModel (implements BaseChatModel Protocol).
No real LLM calls. No real browser.
"""

from __future__ import annotations

import json

import pytest

from open_banca_domain.entities.bank_map import BankMap, StepSpec
from open_banca_llm.mapper.agent import FakeChatModel, MapperAgent


@pytest.fixture()
def valid_map_json() -> str:
    """A minimal valid map.json payload."""
    return json.dumps(
        {
            "bank_id": "banco-general",
            "version": "1.0.0",
            "schema_version": "1",
            "steps": [
                {
                    "step_id": "s1",
                    "action": "navigate",
                    "target": "https://bancogeneral.com",
                    "url": "https://bancogeneral.com",
                    "wait_until": "load",
                },
                {
                    "step_id": "s2",
                    "action": "fill",
                    "target": "#username",
                    "selector": "#username",
                    "value_ref": "<USERNAME>",
                },
                {
                    "step_id": "s3",
                    "action": "click",
                    "target": "#submit",
                    "selector": "#submit",
                },
            ],
        }
    )


@pytest.fixture()
def fake_llm(valid_map_json: str) -> FakeChatModel:
    return FakeChatModel(responses=[valid_map_json])


@pytest.mark.asyncio()
async def test_mapper_produces_valid_bankmap(
    fake_llm: FakeChatModel,
    valid_map_json: str,
) -> None:
    """MapperAgent with FakeChatModel returns a valid BankMap Pydantic entity."""
    agent = MapperAgent(llm_override=fake_llm)

    bank_map = await agent.map_bank(
        bank_id="banco-general",
        credential_ref="vault://banco-general/user",
        sensitive_data={"<USERNAME>": "testuser", "<PASSWORD>": "testpass"},
    )

    assert isinstance(bank_map, BankMap)
    assert bank_map.bank_id == "banco-general"
    assert bank_map.schema_version == "1"
    assert len(bank_map.steps) == 3


@pytest.mark.asyncio()
async def test_mapper_bankmap_steps_are_stepspec(fake_llm: FakeChatModel) -> None:
    """Each step in the returned BankMap is a valid StepSpec instance."""
    agent = MapperAgent(llm_override=fake_llm)
    bank_map = await agent.map_bank(
        bank_id="banco-general",
        credential_ref="vault://test",
        sensitive_data={},
    )
    for step in bank_map.steps:
        assert isinstance(step, StepSpec)
        assert step.step_id
        assert step.action


@pytest.mark.asyncio()
async def test_mapper_strips_markdown_fences(fake_llm: FakeChatModel) -> None:
    """MapperAgent strips markdown code fences from LLM output before parsing."""
    raw_with_fences = "```json\n" + (await _make_simple_map_json()) + "\n```"
    llm = FakeChatModel(responses=[raw_with_fences])
    agent = MapperAgent(llm_override=llm)
    bank_map = await agent.map_bank(
        bank_id="banco-general",
        credential_ref="vault://test",
        sensitive_data={},
    )
    assert isinstance(bank_map, BankMap)


async def _make_simple_map_json() -> str:
    return json.dumps(
        {
            "bank_id": "banco-general",
            "version": "1.0.0",
            "schema_version": "1",
            "steps": [{"step_id": "s1", "action": "navigate", "url": "https://example.com"}],
        }
    )


@pytest.mark.asyncio()
async def test_mapper_injects_bank_id_if_missing() -> None:
    """If LLM omits bank_id, MapperAgent injects it from the argument."""
    no_bank_id_map = json.dumps(
        {
            "version": "1.0.0",
            "schema_version": "1",
            "steps": [],
        }
    )
    llm = FakeChatModel(responses=[no_bank_id_map])
    agent = MapperAgent(llm_override=llm)
    bank_map = await agent.map_bank(
        bank_id="my-bank",
        credential_ref="vault://test",
        sensitive_data={},
    )
    assert bank_map.bank_id == "my-bank"

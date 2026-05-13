"""Tests for Mapper agent security question detection (ADR-0021).

TDD coverage:
1. When FakeChatModel reports a map with prompt_user step, MapperAgent detects it.
2. In interactive mode with mock typer.prompt, interactive_prompt is invoked.
3. In non-interactive mode, no prompt is shown and map is returned unchanged.
4. interactive_prompt with operator skipping (answer 'N') returns None.
5. interactive_prompt with operator accepting stores to vault.
6. System prompt includes 'prompt_user' in the allowed step types list.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from open_banca_llm.mapper.agent import FakeChatModel, MapperAgent, interactive_prompt
from open_banca_llm.mapper.prompts import ALLOWED_STEP_TYPES, SYSTEM_PROMPT


# ── System prompt coverage ────────────────────────────────────────────────────


def test_system_prompt_includes_prompt_user() -> None:
    """SYSTEM_PROMPT must mention prompt_user as an allowed step type."""
    assert "prompt_user" in SYSTEM_PROMPT, (
        "System prompt must include 'prompt_user' in allowed step types (ADR-0021)"
    )


def test_allowed_step_types_includes_prompt_user() -> None:
    """ALLOWED_STEP_TYPES must include 'prompt_user'."""
    assert "prompt_user" in ALLOWED_STEP_TYPES


def test_allowed_step_types_count() -> None:
    """After ADR-0021 there should be 10 allowed step types."""
    assert len(ALLOWED_STEP_TYPES) == 10


# ── Mapper agent with FakeChatModel returns prompt_user step ──────────────────


@pytest.mark.asyncio
async def test_mapper_returns_map_with_prompt_user_step() -> None:
    """FakeChatModel returning a map with prompt_user — MapperAgent validates it."""
    map_with_prompt_user = {
        "bank_id": "banco-test",
        "version": "1.0.0",
        "schema_version": "1",
        "steps": [
            {"step_id": "navigate_login", "action": "navigate", "url": "https://banco-test.com/login"},
            {"step_id": "fill_user", "action": "fill", "selector": "#user", "value_ref": "<USERNAME>"},
            {
                "step_id": "security_question",
                "action": "prompt_user",
                "selector": "#security-answer",
                "question_selector": ".security-question-text",
                "field_key": "security_q_pet",
                "cache_answers": True,
                "timeout_s": 240,
            },
        ],
    }

    fake_llm = FakeChatModel(responses=[json.dumps(map_with_prompt_user)])
    agent = MapperAgent(
        llm_override=fake_llm,
        interactive=False,  # non-interactive — no CLI prompt
    )

    result = await agent.map_bank(
        bank_id="banco-test",
        credential_ref="vault://cred-001",
        sensitive_data={"<USERNAME>": "user", "<PASSWORD>": "pass"},
    )

    prompt_steps = [s for s in result.steps if s.action == "prompt_user"]
    assert len(prompt_steps) == 1, f"Expected 1 prompt_user step, got {len(prompt_steps)}"
    assert (prompt_steps[0].model_extra or {}).get("field_key") == "security_q_pet"


# ── Non-interactive mode does not prompt ──────────────────────────────────────


@pytest.mark.asyncio
async def test_non_interactive_does_not_call_prompt(monkeypatch: Any) -> None:
    """In non-interactive mode, no terminal prompt is shown."""
    map_with_prompt_user = {
        "bank_id": "banco-test",
        "version": "1.0.0",
        "schema_version": "1",
        "steps": [
            {"step_id": "nav", "action": "navigate", "url": "https://banco-test.com"},
            {
                "step_id": "sec_q",
                "action": "prompt_user",
                "selector": "#answer",
                "question_selector": ".question",
                "field_key": "security_q_pet",
                "cache_answers": True,
                "timeout_s": 240,
            },
        ],
    }

    prompted = []

    def _mock_prompt(msg: str, **kwargs: Any) -> str:
        prompted.append(msg)
        return "Y"

    fake_llm = FakeChatModel(responses=[json.dumps(map_with_prompt_user)])
    agent = MapperAgent(
        llm_override=fake_llm,
        interactive=False,  # <-- non-interactive
        vault=None,
    )

    with patch("open_banca_llm.mapper.agent.interactive_prompt") as mock_ipr:
        await agent.map_bank(
            bank_id="banco-test",
            credential_ref="vault://cred-001",
            sensitive_data={},
        )
        mock_ipr.assert_not_called()


# ── interactive_prompt helper ─────────────────────────────────────────────────


def test_interactive_prompt_returns_answer_when_accepted() -> None:
    """interactive_prompt returns the answer when operator accepts."""
    call_count = [0]

    def _fake_prompt(msg: str, **kwargs: Any) -> str:
        call_count[0] += 1
        if call_count[0] == 1:
            return "Y"  # accept preload
        return "Fluffy"  # answer

    result = interactive_prompt(
        bank_id="banco-test",
        field_key="security_q_pet",
        question_selector=".question",
        prompt_fn=_fake_prompt,
    )
    assert result == "Fluffy"


def test_interactive_prompt_returns_none_when_skipped() -> None:
    """interactive_prompt returns None when operator enters 'n'."""
    def _fake_prompt(msg: str, **kwargs: Any) -> str:
        return "n"  # skip

    result = interactive_prompt(
        bank_id="banco-test",
        field_key="security_q_pet",
        question_selector=".question",
        prompt_fn=_fake_prompt,
    )
    assert result is None


def test_interactive_prompt_returns_none_on_empty_answer() -> None:
    """interactive_prompt returns None on empty answer."""
    call_count = [0]

    def _fake_prompt(msg: str, **kwargs: Any) -> str:
        call_count[0] += 1
        if call_count[0] == 1:
            return "Y"
        return ""  # empty answer

    result = interactive_prompt(
        bank_id="banco-test",
        field_key="security_q_pet",
        question_selector=".question",
        prompt_fn=_fake_prompt,
    )
    assert result is None


# ── Interactive mode with vault stores answer ─────────────────────────────────


@pytest.mark.asyncio
async def test_interactive_mode_with_vault_stores_answer() -> None:
    """In interactive mode with vault, answer is stored for detected prompt_user step."""
    map_with_prompt_user = {
        "bank_id": "banco-test",
        "version": "1.0.0",
        "schema_version": "1",
        "steps": [
            {"step_id": "nav", "action": "navigate", "url": "https://banco-test.com"},
            {
                "step_id": "sec_q",
                "action": "prompt_user",
                "selector": "#answer",
                "question_selector": ".question",
                "field_key": "security_q_pet",
                "cache_answers": True,
                "timeout_s": 240,
            },
        ],
    }

    fake_vault = MagicMock()
    fake_vault.store_security_answer = MagicMock()

    # Mock interactive_prompt to return an answer
    with patch("open_banca_llm.mapper.agent.interactive_prompt") as mock_ipr:
        mock_ipr.return_value = "Fluffy"

        fake_llm = FakeChatModel(responses=[json.dumps(map_with_prompt_user)])
        agent = MapperAgent(
            llm_override=fake_llm,
            interactive=True,
            vault=fake_vault,
        )

        await agent.map_bank(
            bank_id="banco-test",
            credential_ref="vault://cred-001",
            sensitive_data={},
        )

        mock_ipr.assert_called_once()
        fake_vault.store_security_answer.assert_called_once()
        call_kwargs = fake_vault.store_security_answer.call_args
        assert call_kwargs.kwargs.get("field_key") == "security_q_pet"

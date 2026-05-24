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

from open_banca_llm.mapper.agent import (
    SECURITY_ANSWER_TOOL_NAME,
    FakeChatModel,
    MapperAgent,
    build_mapper_browser_tools,
    derive_field_key_from_question,
    interactive_prompt,
    prompt_security_answer_for_mapping,
    store_mapper_security_answer_to_vault,
)
from open_banca_llm.mapper.prompts import ALLOWED_STEP_TYPES, SYSTEM_PROMPT

# ── System prompt coverage ────────────────────────────────────────────────────


def test_system_prompt_includes_prompt_user() -> None:
    """SYSTEM_PROMPT must mention prompt_user as an allowed step type."""
    assert "prompt_user" in SYSTEM_PROMPT, (
        "System prompt must include 'prompt_user' in allowed step types (ADR-0021)"
    )


def test_system_prompt_includes_ask_operator_tool() -> None:
    """SYSTEM_PROMPT must instruct use of ask_operator_for_security_answer during mapping."""
    assert "ask_operator_for_security_answer" in SYSTEM_PROMPT


def test_build_mapper_browser_tools_registers_security_action() -> None:
    """Custom security-answer tool is registered on mapper Tools."""
    tools = build_mapper_browser_tools(bank_id="banco_general")
    assert SECURITY_ANSWER_TOOL_NAME in tools.registry.registry.actions


def test_prompt_security_answer_for_mapping_returns_answer() -> None:
    """Mid-run terminal prompt returns operator answer."""
    answers = iter(["mi respuesta"])

    def mock_prompt(_msg: str, **_kw: object) -> str:
        return next(answers)

    result = prompt_security_answer_for_mapping(
        "¿Cuál es tu mascota?",
        bank_id="banco_general",
        prompt_fn=mock_prompt,
    )
    assert result == "mi respuesta"


def test_derive_field_key_from_question_matches_regex() -> None:
    """Auto-derived field_key satisfies ADR-0021 regex."""
    key = derive_field_key_from_question("¿Cuál es el apodo de tu abuelo?")
    assert key.startswith("security_q_")
    assert 3 <= len(key) <= 32
    assert key.replace("_", "").isalnum()


def test_store_mapper_security_answer_to_vault_calls_vault() -> None:
    """Mid-run vault store uses compute_question_hash with live question text."""
    vault = MagicMock()
    store_mapper_security_answer_to_vault(
        vault=vault,
        bank_id="banco_general",
        credential_ref="vault://banco_general/personal",
        field_key="security_q_grandpa_nickname",
        question_text="¿Cuál es el apodo de tu abuelo?",
        answer="Pepe",
    )
    vault.store_security_answer.assert_called_once()
    call_kw = vault.store_security_answer.call_args.kwargs
    assert call_kw["credential_id"] == "vault://banco_general/personal"
    assert call_kw["field_key"] == "security_q_grandpa_nickname"
    assert call_kw["answer"] == "Pepe"
    assert len(call_kw["question_hash"]) == 64


@pytest.mark.asyncio
async def test_security_tool_stores_answer_in_vault_when_configured() -> None:
    """ask_operator_for_security_answer persists to vault when vault is wired."""
    vault = MagicMock()
    tools = build_mapper_browser_tools(
        bank_id="banco_general",
        credential_ref="vault://banco_general/personal",
        vault=vault,
        prompt_fn=lambda _msg, **_kw: "firulais",
    )
    action = tools.registry.registry.actions[SECURITY_ANSWER_TOOL_NAME]
    await action.function(
        params=action.param_model(
            question="¿Cuál es tu primera mascota?",
            field_key="security_q_first_pet",
        ),
        browser_session=MagicMock(),
    )
    vault.store_security_answer.assert_called_once()


def test_prompt_security_answer_for_mapping_empty_returns_none() -> None:
    """Empty answer from operator yields None."""

    def mock_prompt(_msg: str, **_kw: object) -> str:
        return ""

    result = prompt_security_answer_for_mapping("¿Color favorito?", prompt_fn=mock_prompt)
    assert result is None


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
            {
                "step_id": "navigate_login",
                "action": "navigate",
                "url": "https://banco-test.com/login",
            },
            {
                "step_id": "fill_user",
                "action": "fill",
                "selector": "#user",
                "value_ref": "<USERNAME>",
            },
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

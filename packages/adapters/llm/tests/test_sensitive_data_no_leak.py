"""Test: PII_CANARY_PASSWORD NEVER appears in messages received by the LLM.

CLAUDE.md §Security Critical Rules #2:
  "Mapper/Remapper usan `sensitive_data` de browser-use. LLM nunca recibe
  plaintext de creds. Fuzz test instrumenta LiteLLM y verifica."

This test instruments FakeChatModel (which records all received messages) and
asserts that the canary password string does not appear anywhere in the
messages passed to ainvoke() after PIIRedactingChatModel scrubs them.
"""

from __future__ import annotations

import json

import pytest
from browser_use.llm.messages import ContentPartTextParam, SystemMessage, UserMessage

from open_banca_llm.mapper.agent import FakeChatModel, MapperAgent
from open_banca_llm.mapper.pii_filter_adapter import PIIRedactingChatModel
from open_banca_observability.redact import RedactConfig

PII_CANARY_PASSWORD = "S3cr3t_C@nary_P@ssword_XyZ_99182736"


@pytest.fixture()
def redact_config() -> RedactConfig:
    """RedactConfig with the canary password as a literal to scrub."""
    return RedactConfig(secret_canary=PII_CANARY_PASSWORD)


def _messages_contain_canary(messages_list: list[list], canary: str) -> bool:
    """Return True if the canary appears anywhere in any recorded message."""
    for messages in messages_list:
        for msg in messages:
            if isinstance(msg, (SystemMessage, UserMessage)):
                content = msg.content
                if isinstance(content, str) and canary in content:
                    return True
                if isinstance(content, list):
                    for part in content:
                        if hasattr(part, "text") and canary in part.text:
                            return True
    return False


@pytest.mark.asyncio()
async def test_canary_password_not_in_llm_messages(
    redact_config: RedactConfig,
) -> None:
    """Canary password in a UserMessage is scrubbed before reaching FakeChatModel."""
    fake = FakeChatModel()
    wrapped = PIIRedactingChatModel(inner=fake, redact_config=redact_config)

    messages = [
        SystemMessage(role="system", content="You are a mapper."),
        UserMessage(
            role="user",
            content=f"User credential: {PII_CANARY_PASSWORD} — please use it.",
        ),
    ]
    await wrapped.ainvoke(messages)

    assert not _messages_contain_canary(fake.recorded_messages, PII_CANARY_PASSWORD), (
        "Canary password leaked to LLM messages!"
    )


@pytest.mark.asyncio()
async def test_canary_password_not_in_system_prompt(
    redact_config: RedactConfig,
) -> None:
    """Canary password embedded in system prompt is scrubbed."""
    fake = FakeChatModel()
    wrapped = PIIRedactingChatModel(inner=fake, redact_config=redact_config)

    messages = [
        SystemMessage(
            role="system",
            content=f"System: {PII_CANARY_PASSWORD} embedded in system prompt.",
        ),
    ]
    await wrapped.ainvoke(messages)

    assert not _messages_contain_canary(fake.recorded_messages, PII_CANARY_PASSWORD), (
        "Canary password leaked in system prompt!"
    )


@pytest.mark.asyncio()
async def test_canary_in_multipart_content_not_leaked(
    redact_config: RedactConfig,
) -> None:
    """Canary in a ContentPartTextParam within multipart content is scrubbed."""
    fake = FakeChatModel()
    wrapped = PIIRedactingChatModel(inner=fake, redact_config=redact_config)

    messages = [
        UserMessage(
            role="user",
            content=[
                ContentPartTextParam(type="text", text=f"Password: {PII_CANARY_PASSWORD}"),
                ContentPartTextParam(type="text", text="Normal navigation text"),
            ],
        )
    ]
    await wrapped.ainvoke(messages)

    assert not _messages_contain_canary(fake.recorded_messages, PII_CANARY_PASSWORD), (
        "Canary password leaked in multipart text content!"
    )


@pytest.mark.asyncio()
async def test_mapper_agent_sensitive_data_not_in_stub_prompt() -> None:
    """sensitive_data values passed to MapperAgent do not appear in LLM messages.

    In stub mode, MapperAgent calls llm.ainvoke() with the task prompt.
    The task prompt should never contain the actual secret values — only
    placeholder references.
    """
    fake = FakeChatModel(
        responses=[
            json.dumps(
                {
                    "bank_id": "banco-general",
                    "version": "1.0.0",
                    "schema_version": "1",
                    "steps": [],
                }
            )
        ]
    )

    # Wrap fake with PII redact to simulate the full stack
    redact_cfg = RedactConfig(secret_canary=PII_CANARY_PASSWORD)
    pii_wrapped = PIIRedactingChatModel(inner=fake, redact_config=redact_cfg)

    agent = MapperAgent(llm_override=pii_wrapped)

    sensitive_data = {
        "<USERNAME>": "testuser@bank.com",
        "<PASSWORD>": PII_CANARY_PASSWORD,
    }

    await agent.map_bank(
        bank_id="banco-general",
        credential_ref="vault://banco-general/user",
        sensitive_data=sensitive_data,
    )

    # Verify the canary never reached the innermost FakeChatModel
    assert not _messages_contain_canary(fake.recorded_messages, PII_CANARY_PASSWORD), (
        "Canary password appeared in LLM input via MapperAgent!"
    )

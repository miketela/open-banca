"""Tests: get_client() returns a valid Temporal Client using time-skipping env.

These tests do NOT require a running Temporal server.
``WorkflowEnvironment.start_time_skipping()`` spins up a lightweight in-process
test server (Temporalite Go binary, downloaded once by temporalio on first run).
"""

import pytest
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment

from open_banca_orchestrator.client import get_client
from open_banca_orchestrator.config import OrchestratorSettings


@pytest.mark.asyncio
async def test_get_client_returns_client_type() -> None:
    """get_client() returns a temporalio.client.Client instance."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        settings = OrchestratorSettings(
            temporal_address=env.client.service_client.config.target_host,
            temporal_namespace=env.client.namespace,
        )
        client = await get_client(settings)
        assert isinstance(client, Client)


@pytest.mark.asyncio
async def test_get_client_uses_correct_namespace() -> None:
    """get_client() binds the client to the namespace from settings."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        settings = OrchestratorSettings(
            temporal_address=env.client.service_client.config.target_host,
            temporal_namespace=env.client.namespace,
        )
        client = await get_client(settings)
        assert client.namespace == settings.temporal_namespace


@pytest.mark.asyncio
async def test_get_client_without_explicit_settings() -> None:
    """get_client() falls back to get_settings() when no settings passed."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        # Override the default address so the client connects to the test server
        import os  # noqa: PLC0415

        original = os.environ.get("OPEN_BANCA_TEMPORAL_ADDRESS")
        os.environ["OPEN_BANCA_TEMPORAL_ADDRESS"] = env.client.service_client.config.target_host
        try:
            client = await get_client()
            assert isinstance(client, Client)
        finally:
            if original is None:
                os.environ.pop("OPEN_BANCA_TEMPORAL_ADDRESS", None)
            else:
                os.environ["OPEN_BANCA_TEMPORAL_ADDRESS"] = original

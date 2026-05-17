"""Temporal client factory.

Provides a reusable async helper for obtaining a connected ``temporalio.client.Client``.
Used by both the worker entry-point and the API adapter (when dispatching workflows).

Usage::

    from open_banca_orchestrator.client import get_client

    async def main() -> None:
        client = await get_client()
        # client.start_workflow(...)
"""

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from open_banca_orchestrator.config import OrchestratorSettings, get_settings


async def get_client(settings: OrchestratorSettings | None = None) -> Client:
    """Connect to the Temporal frontend and return a ready ``Client``.

    Args:
        settings: Optional settings override.  When ``None``, reads from env.

    Returns:
        A connected ``temporalio.client.Client`` bound to the configured namespace.

    Raises:
        temporalio.service.RPCError: if the Temporal server is unreachable.
    """
    cfg = settings or get_settings()
    return await Client.connect(
        cfg.temporal_address,
        namespace=cfg.temporal_namespace,
        data_converter=pydantic_data_converter,
    )

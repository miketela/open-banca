"""Background task: expire pending remap proposals past their 24h TTL.

Runs every hour via asyncio.  If a proposal expires and its associated
Temporal workflow is waiting, we signal remap_rejected so the workflow
transitions to human_required final state.

Usage (in FastAPI lifespan):
    from open_banca_api.tasks.expire_proposals import start_expire_task, stop_expire_task

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = start_expire_task()
        yield
        stop_expire_task(task)
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TTL_SWEEP_INTERVAL_SECONDS = int(os.environ.get("OPEN_BANCA_PROPOSAL_SWEEP_INTERVAL", "3600"))


def _get_connection() -> Any:
    """Build a storage connection for the sweep task (same env-driven config as deps.py)."""
    from open_banca_storage.connection import ConnectionPool, PassthroughKeyDerivation
    from open_banca_storage.migrations import migrate

    db_path = Path(os.environ.get("OPEN_BANCA_DB_PATH", "/data/open_banca.db"))
    passphrase = os.environ.get("OPEN_BANCA_MASTER_PASSPHRASE", "dev-insecure-passphrase")

    try:
        from open_banca_storage.kdf import Argon2idKeyDerivation

        kdf = Argon2idKeyDerivation()
    except Exception:  # pragma: no cover
        kdf = PassthroughKeyDerivation()  # type: ignore[assignment]

    pool = ConnectionPool(db_path=db_path, passphrase=passphrase, key_derivation=kdf)
    conn = pool.get()
    migrate(conn)
    return conn


async def _sweep_once(temporal_client: Any | None = None) -> int:
    """Run a single TTL sweep.  Returns the number of proposals expired."""
    from open_banca_storage.repositories.proposal_repo import ProposalRepository

    try:
        conn = await asyncio.get_running_loop().run_in_executor(None, _get_connection)
        repo = ProposalRepository(conn)
        expired_ids: list[str] = await asyncio.get_running_loop().run_in_executor(
            None, repo.expire_old
        )
    except Exception as exc:
        logger.error("expire_proposals sweep failed: %s", exc, exc_info=True)
        return 0

    if expired_ids:
        logger.info("expire_proposals: %d proposals expired", len(expired_ids))
        if temporal_client is not None:
            for proposal_id in expired_ids:
                await _signal_expired(temporal_client, proposal_id)

    return len(expired_ids)


async def _signal_expired(temporal_client: Any, proposal_id: str) -> None:
    """Signal remap_rejected to the Temporal workflow waiting on this proposal."""
    try:
        handle = temporal_client.get_workflow_handle(proposal_id)  # type: ignore[attr-defined]
        await handle.signal("remap_rejected", "ttl_expired")
        logger.info("Signalled remap_rejected for expired proposal %s", proposal_id)
    except Exception as exc:
        # Workflow may already be gone — log and continue
        logger.warning("Could not signal expired proposal %s: %s", proposal_id, exc)


async def _expire_loop(temporal_client: Any | None, interval: int) -> None:
    """Periodic loop that sweeps for expired proposals every *interval* seconds."""
    logger.info("expire_proposals task started (interval=%ds)", interval)
    while True:
        await asyncio.sleep(interval)
        await _sweep_once(temporal_client)


def start_expire_task(
    temporal_client: Any | None = None,
    interval: int = _TTL_SWEEP_INTERVAL_SECONDS,
) -> asyncio.Task[None]:
    """Start the background TTL sweep task.

    Args:
        temporal_client: Optional Temporal client for signalling expired proposals.
        interval: Sweep interval in seconds (default: 3600).

    Returns:
        The asyncio.Task — cancel it on shutdown via stop_expire_task().
    """
    return asyncio.create_task(
        _expire_loop(temporal_client, interval),
        name="expire_proposals",
    )


def stop_expire_task(task: asyncio.Task[None]) -> None:
    """Cancel the background sweep task gracefully."""
    if not task.done():
        task.cancel()
        logger.info("expire_proposals task cancelled")

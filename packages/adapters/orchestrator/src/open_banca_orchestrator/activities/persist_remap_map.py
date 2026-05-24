"""PersistRemapMapActivity — atomically write an approved map.json after HITL remap."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field
from temporalio import activity
from temporalio.exceptions import ApplicationError

from open_banca_domain.entities.bank_map import BankMap

logger = logging.getLogger(__name__)

_BANKS_DIR = Path(__file__).resolve().parents[5] / "banks"


class PersistRemapMapInput(BaseModel):
    """Input for PersistRemapMapActivity."""

    bank_id: str = Field(description="Bank identifier")
    updated_map: BankMap = Field(description="Approved map to persist")
    proposal_id: str = Field(description="Remap proposal ID for audit/idempotency")


class PersistRemapMapResult(BaseModel):
    """Result from PersistRemapMapActivity."""

    map_path: str
    version: str
    persisted: bool = Field(
        description="False when map on disk already matches (idempotent no-op)"
    )


def _banks_root() -> Path:
    env_val = os.environ.get("OPEN_BANCA_BANKS_ROOT")
    if env_val:
        return Path(env_val)
    return _BANKS_DIR


def _persist_sync(inp: PersistRemapMapInput) -> PersistRemapMapResult:
    bank_dir = _banks_root() / inp.bank_id
    map_path = bank_dir / "map.json"
    bank_dir.mkdir(parents=True, exist_ok=True)

    new_payload = inp.updated_map.model_dump(mode="json")
    new_text = json.dumps(new_payload, indent=2, ensure_ascii=False) + "\n"

    if map_path.exists():
        existing = map_path.read_text(encoding="utf-8")
        if existing.strip() == new_text.strip():
            return PersistRemapMapResult(
                map_path=str(map_path),
                version=inp.updated_map.version,
                persisted=False,
            )

    tmp_path = map_path.with_suffix(".json.tmp")
    tmp_path.write_text(new_text, encoding="utf-8")
    os.replace(tmp_path, map_path)

    logger.info(
        "persist_remap_map: bank=%s proposal=%s version=%s",
        inp.bank_id,
        inp.proposal_id,
        inp.updated_map.version,
    )
    return PersistRemapMapResult(
        map_path=str(map_path),
        version=inp.updated_map.version,
        persisted=True,
    )


@activity.defn(name="PersistRemapMapActivity")
async def persist_remap_map(input: PersistRemapMapInput) -> PersistRemapMapResult:  # noqa: A002
    """Write approved map.json atomically (idempotent when content unchanged)."""
    try:
        return await asyncio.to_thread(_persist_sync, input)
    except OSError as exc:
        raise ApplicationError(
            f"Failed to persist map for bank={input.bank_id}: {exc}",
            non_retryable=False,
        ) from exc

"""LoadBankMapActivity — load map.json for a bank from the maps repo."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pydantic import BaseModel, Field
from temporalio import activity
from temporalio.exceptions import ApplicationError

from open_banca_domain.entities.bank_map import BankMap

_BANKS_DIR = Path(__file__).resolve().parents[5] / "banks"


class LoadBankMapInput(BaseModel):
    """Input for LoadBankMapActivity."""

    bank_id: str = Field(description="Bank identifier (e.g. banco_general)")


class LoadBankMapResult(BaseModel):
    """Result from LoadBankMapActivity."""

    bank_map: BankMap


def _load_sync(bank_id: str) -> BankMap:
    map_path = _BANKS_DIR / bank_id / "map.json"
    if not map_path.exists():
        raise ApplicationError(
            f"map.json not found for bank_id={bank_id!r}",
            non_retryable=True,
            type="map_not_found",
        )
    data = json.loads(map_path.read_text(encoding="utf-8"))
    return BankMap.model_validate(data)


@activity.defn(name="LoadBankMapActivity")
async def load_bank_map(input: LoadBankMapInput) -> LoadBankMapResult:  # noqa: A002
    """Load and validate map.json for the given bank."""
    bank_map = await asyncio.to_thread(_load_sync, input.bank_id)
    return LoadBankMapResult(bank_map=bank_map)

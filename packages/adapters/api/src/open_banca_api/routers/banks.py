"""GET /banks — list supported banks."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from open_banca_api.auth import verify_bearer

logger = logging.getLogger(__name__)

# Path to the banks directory.
# parents[6] = worktree root (from .../packages/adapters/api/src/open_banca_api/routers/banks.py)
# Override at runtime via OPEN_BANCA_BANKS_DIR env var for alternative deployments.
import os as _os  # noqa: E402

_BANKS_DIR: Path = Path(
    _os.environ.get(
        "OPEN_BANCA_BANKS_DIR",
        str(Path(__file__).parents[6] / "packages" / "banks"),
    )
)

router = APIRouter(
    prefix="/banks",
    tags=["banks"],
    dependencies=[Depends(verify_bearer)],
)


def _discover_banks() -> list[dict[str, Any]]:
    """Scan the banks/ directory and return bank metadata from project.json files."""
    banks: list[dict[str, Any]] = []

    if not _BANKS_DIR.exists():
        logger.warning("Banks directory not found: %s", _BANKS_DIR)
        return banks

    for bank_dir in sorted(_BANKS_DIR.iterdir()):
        if not bank_dir.is_dir():
            continue

        bank_id = bank_dir.name

        # Try to load metadata from project.json
        project_json = bank_dir / "project.json"
        display_name = bank_id.replace("_", " ").title()
        country = "PA"  # default; can be overridden by project.json
        map_version = "unknown"

        if project_json.exists():
            try:
                meta = json.loads(project_json.read_text())
                display_name = meta.get("displayName", display_name)
                country = meta.get("country", country)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not parse %s: %s", project_json, exc)

        # Try to read map version from map.json
        map_json = bank_dir / "map.json"
        if map_json.exists():
            try:
                m = json.loads(map_json.read_text())
                map_version = m.get("version", map_version)
            except (json.JSONDecodeError, OSError):
                pass

        banks.append(
            {
                "bank_id": bank_id,
                "display_name": display_name,
                "country": country,
                "map_version": map_version,
                "last_successful_scrape": None,  # populated once storage has data
                "circuit_status": "ok",
            }
        )

    return banks


@router.get(
    "",
    summary="List supported banks",
    description=(
        "Returns list of supported banks with bank_id, display_name, country, "
        "map_version, last_successful_scrape, and circuit_status."
    ),
)
async def list_banks() -> list[dict[str, Any]]:
    """GET /banks — discover banks from directory structure."""
    return _discover_banks()

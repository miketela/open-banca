"""GET /banks — list supported banks. Stub."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from open_banca_api.auth import verify_bearer

router = APIRouter(
    prefix="/banks",
    tags=["banks"],
    dependencies=[Depends(verify_bearer)],
)


@router.get(
    "",
    summary="List supported banks",
    description=(
        "Returns list of supported banks with bank_id, display_name, country, "
        "map_version, last_successful_scrape, and circuit_status."
    ),
)
async def list_banks() -> None:
    """GET /banks stub."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not implemented — wired in task #31",
    )

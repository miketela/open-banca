"""GET /accounts — list known accounts. Stub."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from open_banca_api.auth import verify_bearer

router = APIRouter(
    prefix="/accounts",
    tags=["accounts"],
    dependencies=[Depends(verify_bearer)],
)


@router.get(
    "",
    summary="List known accounts",
    description="Returns accounts cached after first successful scrape.",
)
async def list_accounts() -> None:
    """GET /accounts stub."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not implemented — wired in task #31",
    )

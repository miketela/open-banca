"""POST /webhooks/test — fire a synthetic signed webhook. Stub."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from open_banca_api.auth import verify_bearer
from open_banca_api.schemas.webhooks import WebhookTestRequest

router = APIRouter(
    prefix="/webhooks",
    tags=["webhooks"],
    dependencies=[Depends(verify_bearer)],
)


@router.post(
    "/test",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Fire a synthetic webhook",
    description=(
        "Sends a synthetic signed webhook event to the configured endpoint "
        "to verify connectivity and HMAC-SHA256 signature validation."
    ),
)
async def test_webhook(body: WebhookTestRequest) -> None:
    """POST /webhooks/test stub."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not implemented — wired in task #31",
    )

"""POST /webhooks/test — fire a synthetic signed webhook."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from open_banca_api.auth import verify_bearer
from open_banca_api.schemas.webhooks import WebhookTestRequest

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/webhooks",
    tags=["webhooks"],
    dependencies=[Depends(verify_bearer)],
)


def _sign_payload(payload_json: str, secret: str) -> str:
    """Return HMAC-SHA256 hex signature for a payload."""
    return hmac.new(
        secret.encode(),
        payload_json.encode(),
        hashlib.sha256,
    ).hexdigest()


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
    """POST /webhooks/test — dispatch a synthetic webhook to the configured URL."""
    webhook_url = os.environ.get("OPEN_BANCA_WEBHOOK_URL", "")
    webhook_secret = os.environ.get("OPEN_BANCA_WEBHOOK_SECRET", "dev-insecure-webhook-secret")

    if not webhook_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "webhook_url_not_configured",
                "message": "Set OPEN_BANCA_WEBHOOK_URL to enable webhook dispatch.",
            },
        )

    payload: dict[str, object] = {
        "id": str(uuid4()),
        "event_type": body.event_type,
        "payload": {"test": True, "triggered_at": datetime.now(UTC).isoformat()},
        "dispatched_at": datetime.now(UTC).isoformat(),
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    signature = _sign_payload(payload_json, webhook_secret)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                webhook_url,
                content=payload_json,
                headers={
                    "Content-Type": "application/json",
                    "X-OpenBanca-Signature": f"sha256={signature}",
                    "X-OpenBanca-Event": body.event_type,
                },
            )
        if resp.status_code >= 400:
            logger.warning(
                "Webhook test delivery failed: url=%s status=%d", webhook_url, resp.status_code
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "webhook_delivery_failed",
                    "http_status": resp.status_code,
                    "message": f"Remote endpoint returned {resp.status_code}.",
                },
            )
    except httpx.RequestError as exc:
        logger.error("Webhook test HTTP error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "webhook_network_error",
                "message": str(exc),
            },
        ) from exc

    logger.info("Webhook test dispatched: event_type=%s url=%s", body.event_type, webhook_url)

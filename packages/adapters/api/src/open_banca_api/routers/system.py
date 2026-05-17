"""System endpoints: GET /health and GET /time (real implementations — not stubs)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from open_banca_api.config import get_settings
from open_banca_api.schemas.system import HealthResponse, TimeResponse

router = APIRouter(tags=["system"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Returns 200 OK when the API process is running.",
)
async def health() -> HealthResponse:
    """Liveness check — no auth required."""
    settings = get_settings()
    return HealthResponse(status="ok", version=settings.app_version)


@router.get(
    "/time",
    response_model=TimeResponse,
    summary="Current UTC time (REQ-010)",
    description="Returns current UTC time as ISO-8601 string.",
)
async def get_time() -> TimeResponse:
    """Return current UTC time. No auth required (REQ-010)."""
    now = datetime.now(UTC)
    return TimeResponse(utc=now, iso8601=now.isoformat())

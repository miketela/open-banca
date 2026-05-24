"""System endpoints: GET /health, /healthz, /readyz, and GET /time."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from open_banca_api.config import Settings, get_settings
from open_banca_api.dependencies import (
    TemporalOrchestratorAdapter,
    check_database_ready,
    get_storage_connection,
    get_temporal_client,
)
from open_banca_api.schemas.system import (
    HealthResponse,
    ReadinessChecks,
    ReadinessResponse,
    TimeResponse,
)

router = APIRouter(tags=["system"])


def _health_payload(settings: Settings) -> HealthResponse:
    return HealthResponse(status="ok", version=settings.app_version)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Returns 200 OK when the API process is running.",
)
async def health(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    """Liveness check — no auth required."""
    return _health_payload(settings)


@router.get(
    "/healthz",
    response_model=HealthResponse,
    summary="Liveness probe (alias)",
    description="Alias of GET /health for Docker and Kubernetes liveness probes.",
)
async def healthz(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    """Liveness check alias — no auth required."""
    return _health_payload(settings)


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Returns 200 when Temporal workers are reachable or the database is accessible; "
        "503 otherwise."
    ),
    responses={
        200: {"description": "At least one dependency is healthy."},
        503: {"description": "Temporal and database checks both failed."},
    },
)
async def readyz(
    settings: Annotated[Settings, Depends(get_settings)],
    orchestrator: Annotated[TemporalOrchestratorAdapter, Depends(get_temporal_client)],
    conn: Annotated[object, Depends(get_storage_connection)],
) -> JSONResponse:
    """Readiness check — no auth required."""
    temporal_ok = await orchestrator.async_check_readiness()
    database_ok = check_database_ready(conn)
    ready = temporal_ok or database_ok
    body = ReadinessResponse(
        status="ready" if ready else "not_ready",
        version=settings.app_version,
        checks=ReadinessChecks(temporal=temporal_ok, database=database_ok),
    )
    return JSONResponse(
        status_code=200 if ready else 503,
        content=body.model_dump(),
    )


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

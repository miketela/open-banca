"""Schemas for system endpoints: /time, /health, /healthz, /readyz."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TimeResponse(BaseModel):
    """GET /time response — current UTC time in ISO-8601 format."""

    model_config = ConfigDict(frozen=True)

    utc: datetime
    iso8601: str


class HealthResponse(BaseModel):
    """GET /health and GET /healthz response."""

    model_config = ConfigDict(frozen=True)

    status: str = "ok"
    version: str


class ReadinessChecks(BaseModel):
    """Individual dependency checks for GET /readyz."""

    model_config = ConfigDict(frozen=True)

    temporal: bool = Field(description="Temporal frontend reachable with active worker pollers.")
    database: bool = Field(description="Encrypted SQLite database accessible.")


class ReadinessResponse(BaseModel):
    """GET /readyz response — 200 when ready, 503 when not."""

    model_config = ConfigDict(frozen=True)

    status: str = Field(description="'ready' or 'not_ready'.")
    version: str
    checks: ReadinessChecks

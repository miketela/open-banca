"""Schemas for system endpoints: /time, /health."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TimeResponse(BaseModel):
    """GET /time response — current UTC time in ISO-8601 format."""

    model_config = ConfigDict(frozen=True)

    utc: datetime
    iso8601: str


class HealthResponse(BaseModel):
    """GET /health response."""

    model_config = ConfigDict(frozen=True)

    status: str = "ok"
    version: str

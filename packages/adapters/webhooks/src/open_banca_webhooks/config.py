"""Webhook dispatcher configuration via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebhookSettings(BaseSettings):
    """Configuration for the webhook dispatcher."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="OPEN_BANCA_WEBHOOK_",
        case_sensitive=False,
        extra="ignore",
    )

    target_url: str = Field(
        default="",
        description="Delivery endpoint for webhook events.",
    )
    secret: str = Field(
        default="dev-insecure-webhook-secret",
        description="HMAC-SHA256 signing secret.",
    )
    db_path: Path = Field(
        default=Path("./webhook_outbox.db"),
        description="SQLite outbox database path.",
    )
    http_timeout: float = Field(
        default=10.0,
        description="HTTP request timeout seconds.",
    )
    poll_interval: float = Field(
        default=5.0,
        description="Worker loop poll interval seconds.",
    )


@lru_cache
def get_settings() -> WebhookSettings:
    """Cached settings singleton."""
    return WebhookSettings()


__all__ = ["WebhookSettings", "get_settings"]

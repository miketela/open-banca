"""Application configuration via pydantic-settings (env vars, .env file)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration for open-banca API."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Auth
    open_banca_api_token: str = Field(
        default="dev-insecure-token",
        description="Bearer token for API auth (single-org).",
        validation_alias=AliasChoices("OPEN_BANCA_API_TOKEN", "API_KEY"),
    )

    # Rate limiting
    rate_limit_scrape_per_minute: int = Field(
        default=60,
        description="Max scrape requests per bearer token per minute.",
    )

    # Security canary values (CI injection for redact tests)
    secret_canary_value: str = Field(
        default="",
        description="Canary secret that must never appear in responses.",
    )

    # PII canary prefix — any env starting with PII_CANARY_ is collected by middleware
    # Individual PII_CANARY_* vars are resolved dynamically via model_extra

    # API metadata
    app_title: str = "open-banca API"
    app_version: str = "0.1.0"
    openapi_version: str = "3.1.0"

    def pii_canary_values(self) -> list[str]:
        """Return all PII_CANARY_* env values (empty strings excluded)."""
        import os

        return [
            v
            for k, v in os.environ.items()
            if k.upper().startswith("PII_CANARY_") and v
        ]


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()

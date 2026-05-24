"""Request/response schemas for POST /credentials."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CredentialStoreRequest(BaseModel):
    """Payload for POST /credentials — stores encrypted bank credentials in the vault."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(..., description="Bank identifier (e.g. 'banco_general').")
    username: str = Field(..., min_length=1, description="Bank login username.")
    password: str = Field(..., min_length=1, description="Bank login password.")
    pin: str | None = Field(
        default=None,
        description="Optional PIN (required by some banks, e.g. banco_general).",
    )
    label: str | None = Field(
        default=None,
        description=(
            "Credential set label used as credential_ref prefix in POST /scrape. "
            "Defaults to bank_id."
        ),
    )


class StoredCredentialItem(BaseModel):
    """One stored credential row (no plaintext)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str


class CredentialStoreResponse(BaseModel):
    """Response after storing credentials — use credential_ref in POST /scrape."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str
    credential_ref: str = Field(
        ...,
        description="Opaque ref to pass as 'credentials' in POST /scrape.",
    )
    stored: list[StoredCredentialItem]

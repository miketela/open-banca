"""Credential entity — reference only, no plaintext."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Credential(BaseModel):
    """Reference to a stored credential. Plaintext lives in the secret vault."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    bank: str
    credential_ref: str
    label: str

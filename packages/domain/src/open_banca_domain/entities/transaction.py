"""Transaction entity — canonical representation of a bank movement."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class Transaction(BaseModel):
    """Immutable record of a single bank transaction post-validation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    account_id: str
    posted_at: datetime
    value_at: datetime
    amount: Decimal
    currency: str
    description: str
    fingerprint_hash: str
    embedded_id: str | None = None
    transfer_match_id: str | None = None

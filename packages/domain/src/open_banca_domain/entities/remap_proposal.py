"""RemapProposal entity — LLM-generated map patch pending approval."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class RemapStatus(StrEnum):
    """Lifecycle states for a remap proposal."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    EXPIRED = "expired"


class RemapProposal(BaseModel):
    """Proposed patch to a BankMap, awaiting Judge decision or human approval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    bank: str
    breakage_id: str
    judge_decision: str
    confidence: float
    risk: str
    patch_diff: str
    status: RemapStatus
    expires_at: datetime

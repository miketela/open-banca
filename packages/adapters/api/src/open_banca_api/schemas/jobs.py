"""Request/response schemas for job management endpoints (ADR-0021)."""
from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, Field, field_validator

_FIELD_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,32}$")

# Unicode categories permitted in answer strings (T27a defence)
# L = Letter, N = Number, P = Punctuation, Z = Separator, S = Symbol
_ALLOWED_UNICODE_CATEGORIES = frozenset({"L", "N", "P", "Z", "S"})
_FORBIDDEN_UNICODE_CATEGORIES = frozenset({"C"})  # Cc (control) + Cf (format)


def _validate_answer_charset(value: str) -> str:
    """Reject Unicode control characters and format characters (T27a).

    Allowed: Unicode categories L*, N*, P*, Z*, S* (letters, numbers, punctuation,
    separators, symbols).
    Rejected: Category Cc (control) and Cf (format chars incl. embed direction marks).
    """
    for ch in value:
        cat = unicodedata.category(ch)
        major = cat[0]
        if major == "C":
            raise ValueError(
                f"Answer contains forbidden Unicode character U+{ord(ch):04X} "
                f"(category {cat!r}): control and format characters are not allowed"
            )
    return value


class HumanInputRequest(BaseModel):
    """Request body for POST /jobs/{id}/human-input (ADR-0021).

    Resolves a pending prompt_user step by delivering the answer to the workflow.
    """

    field_key: str = Field(
        description="Stable key identifying the security question (regex ^[a-z][a-z0-9_]{2,32}$)",
        examples=["security_q_mother_color"],
    )
    answer: str = Field(
        description="The plaintext answer (max 256 bytes UTF-8, no control/format chars)",
        examples=["rojo"],
    )
    persist: bool = Field(
        default=True,
        description="Cache the answer in the vault for future jobs (TTL 90 days)",
    )

    @field_validator("field_key")
    @classmethod
    def validate_field_key(cls, v: str) -> str:
        if not _FIELD_KEY_PATTERN.match(v):
            raise ValueError(
                f"field_key {v!r} must match ^[a-z][a-z0-9_]{{2,32}}$ "
                "(lowercase letters, digits, underscores; 3-33 chars; must start with letter)"
            )
        return v

    @field_validator("answer")
    @classmethod
    def validate_answer(cls, v: str) -> str:
        # Length cap: 256 bytes UTF-8 (T27a)
        if len(v.encode("utf-8")) > 256:
            raise ValueError("answer exceeds 256-byte UTF-8 limit")
        if not v:
            raise ValueError("answer must not be empty")
        return _validate_answer_charset(v)

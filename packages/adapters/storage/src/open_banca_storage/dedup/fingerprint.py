"""Deterministic fingerprint helper for transaction deduplication.

Fingerprint = SHA-256 hex of a canonical UTF-8 string composed from:
    account_id | posted_at (ISO-8601 UTC) | value_at (ISO-8601 UTC)
    | amount (str Decimal) | description_normalized (NFKC)

The pipe character is used as separator because it cannot appear in
the component values after normalisation.  The result is stable across
Python runs (no random salt, no locale-dependent formatting).
"""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import datetime
from decimal import Decimal


def _normalise_description(description: str) -> str:
    """Apply Unicode NFKC normalisation and collapse whitespace.

    NFKC converts compatibility equivalents (e.g. full-width digits,
    ligatures, accented variants with different code-points) to their
    canonical composed form so that semantically identical strings produce
    the same hash regardless of how the bank serialised them.
    """
    normalised = unicodedata.normalize("NFKC", description)
    # Collapse any sequence of whitespace to a single space and strip ends.
    return " ".join(normalised.split())


def compute_fingerprint(
    account_id: str,
    posted_at: datetime,
    value_at: datetime,
    amount: Decimal,
    description: str,
) -> str:
    """Return a hex SHA-256 fingerprint for the given canonical fields.

    Args:
        account_id: UUID of the owning account row.
        posted_at: Posting datetime (timezone-aware UTC).
        value_at: Value datetime (timezone-aware UTC).
        amount: Transaction amount as Decimal (sign preserved).
        description: Raw description string from the bank.

    Returns:
        64-character lowercase hex string.

    Example::

        fp = compute_fingerprint(
            account_id="acc-1",
            posted_at=datetime(2024, 1, 15, 0, 0, tzinfo=UTC),
            value_at=datetime(2024, 1, 15, 0, 0, tzinfo=UTC),
            amount=Decimal("-123.45"),
            description="Pago de servicios",
        )
    """
    desc_norm = _normalise_description(description)
    canonical = (
        f"{account_id}|{posted_at.isoformat()}|{value_at.isoformat()}|{amount!s}|{desc_norm}"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

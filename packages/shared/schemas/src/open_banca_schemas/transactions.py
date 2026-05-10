"""Transaction schemas — re-exports + factory helpers.

Re-exports the canonical ``Transaction`` entity from ``open_banca_domain``
and provides a factory helper for constructing test fixtures.
"""

from __future__ import annotations

import hashlib
import unicodedata
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from open_banca_domain.entities.transaction import Transaction

# Public re-export.
TransactionSchema = Transaction


def _compute_fingerprint(
    account_id: str,
    posted_at: datetime,
    value_at: datetime,
    amount: Decimal,
    description: str,
) -> str:
    """Inline fingerprint computation to avoid circular import with storage.

    Mirrors ``open_banca_storage.dedup.fingerprint.compute_fingerprint``
    exactly — same canonical string, same SHA-256.
    """
    desc_norm = " ".join(unicodedata.normalize("NFKC", description).split())
    canonical = (
        f"{account_id}"
        f"|{posted_at.isoformat()}"
        f"|{value_at.isoformat()}"
        f"|{amount!s}"
        f"|{desc_norm}"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Factory helper ────────────────────────────────────────────────────────────


def make_transaction(
    *,
    id: str | None = None,
    account_id: str,
    posted_at: datetime | None = None,
    value_at: datetime | None = None,
    amount: Decimal = Decimal("100.00"),
    currency: str = "USD",
    description: str = "Test payment",
    embedded_id: str | None = None,
    transfer_match_id: str | None = None,
    fingerprint_hash: str | None = None,
) -> Transaction:
    """Construct a ``Transaction`` with auto-computed fingerprint.

    The ``fingerprint_hash`` is computed from the canonical fields unless
    explicitly overridden.  This mirrors the production flow where the
    parser/use-case layer computes the fingerprint before persisting.

    Args:
        id: UUID string for the transaction.  Auto-generated if None.
        account_id: UUID of the owning account.
        posted_at: Posting datetime (UTC-aware).  Defaults to now.
        value_at: Value datetime (UTC-aware).  Defaults to ``posted_at``.
        amount: Decimal amount (negative = debit, positive = credit).
        currency: ISO 4217 code.
        description: Raw description from the bank.
        embedded_id: Bank-supplied stable ID if available.
        transfer_match_id: UUID of matched counterpart if known.
        fingerprint_hash: Override computed fingerprint (rare — for tests
            that intentionally duplicate a hash).

    Returns:
        A frozen ``Transaction`` Pydantic model.
    """
    now = datetime.now(tz=UTC)
    _posted_at = posted_at or now
    _value_at = value_at or _posted_at
    _fp = fingerprint_hash or _compute_fingerprint(
        account_id=account_id,
        posted_at=_posted_at,
        value_at=_value_at,
        amount=amount,
        description=description,
    )
    return Transaction(
        id=id or str(uuid.uuid4()),
        account_id=account_id,
        posted_at=_posted_at,
        value_at=_value_at,
        amount=amount,
        currency=currency,
        description=description,
        fingerprint_hash=_fp,
        embedded_id=embedded_id,
        transfer_match_id=transfer_match_id,
    )

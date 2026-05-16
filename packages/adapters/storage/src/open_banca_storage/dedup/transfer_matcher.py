"""Fuzzy transfer matcher — Level 3 deduplication.

Identifies pairs of transactions (debit in account A, credit in account B)
that represent the same internal transfer operation.  The match is heuristic:

  - Same absolute amount (sign opposite: one negative, one positive).
  - Date within ±3 days (posting date difference).
  - Both transactions not yet linked to a transfer pair.

When a match is found, ``transfer_match_id`` on both sides is set to the
*other* transaction's UUID, creating a bidirectional cross-link.

Level 3 does NOT deduplicate (does not discard either transaction).
It only annotates both rows so downstream consumers can identify and
exclude double-counting.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from open_banca_domain.entities.transaction import Transaction

# Configurable window — docs specify ±3 days.
TRANSFER_WINDOW_DAYS: int = 3


class TransferMatcher:
    """Match debit/credit pairs across accounts.

    Usage::

        matcher = TransferMatcher()
        pairs = matcher.find_pairs(candidates)
        # pairs: list of (tx_a, tx_b) where tx_a is the debit side

    Args:
        window_days: Maximum posting-date difference in days (default 3).
    """

    def __init__(self, window_days: int = TRANSFER_WINDOW_DAYS) -> None:
        self._window = timedelta(days=window_days)

    def find_pairs(
        self,
        transactions: list[Transaction],
    ) -> list[tuple[Transaction, Transaction]]:
        """Identify transfer pairs within *transactions*.

        Rules:
        - One side has amount < 0 (debit), the other amount > 0 (credit).
        - abs(amount_a) == abs(amount_b).
        - abs(posted_at_a - posted_at_b) <= window_days.
        - Accounts are different (same-account transfers are not meaningful).
        - Neither side already has a ``transfer_match_id`` set.

        Each transaction is matched at most once (greedy, chronological order).

        Returns:
            List of (debit_tx, credit_tx) pairs.  Order within the pair is
            debit first, credit second.
        """
        # Only consider unlinked transactions.
        unlinked = [t for t in transactions if t.transfer_match_id is None]

        # Separate into debits and credits by sign.
        debits = [t for t in unlinked if t.amount < Decimal("0")]
        credits = [t for t in unlinked if t.amount > Decimal("0")]

        matched_ids: set[str] = set()
        pairs: list[tuple[Transaction, Transaction]] = []

        for debit in debits:
            if debit.id in matched_ids:
                continue
            abs_debit = abs(debit.amount)
            for credit in credits:
                if credit.id in matched_ids:
                    continue
                if credit.account_id == debit.account_id:
                    # Same account — not a transfer between accounts.
                    continue
                if abs(credit.amount) != abs_debit:
                    continue
                date_diff = abs((debit.posted_at - credit.posted_at).total_seconds())
                window_seconds = self._window.total_seconds()
                if date_diff > window_seconds:
                    continue
                # Match found.
                pairs.append((debit, credit))
                matched_ids.add(debit.id)
                matched_ids.add(credit.id)
                break  # Each debit matches at most one credit.

        return pairs

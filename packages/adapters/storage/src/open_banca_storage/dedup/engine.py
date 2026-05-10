"""DedupEngine — orchestrates 3-level deduplication and cursor management.

Algorithm (strict order):

1. **Level 1 - embedded_id**: if ``Transaction.embedded_id`` is not None and
   the value exists in ``dedup_index`` (level='embedded'), skip the transaction
   (it is already persisted with the bank-supplied stable ID).

2. **Level 2 - fingerprint**: compute ``compute_fingerprint(...)`` and check
   ``dedup_index`` for a hit.  On hit, skip.  On miss, insert the transaction
   and record the fingerprint in ``dedup_index``.

3. **Level 3 - fuzzy transfer**: for each newly inserted transaction, query the
   DB for an unlinked counterpart in *another* account with the opposite sign,
   same absolute amount, and posting date within the ±3-day window.  This
   cross-DB lookup ensures transfers are matched even when the two sides arrive
   in *separate* ``ingest()`` calls (e.g. account A scraped today, account B
   tomorrow on incremental runs).  In-batch matching also handles the common
   case where both sides arrive together.

Level 3 does NOT deduplicate (does not discard either transaction).  It only
annotates both rows so downstream consumers can identify and exclude
double-counting.

Cursor management (Story 2 AC):

- ``get_cursor(account_id)``: returns ``MAX(posted_at)`` from the
  ``transactions`` table for the given account.  Deliberately derived from
  the transactions table rather than a separate cursor row so it always
  reflects actual persisted data, and because the existing ``cursors`` table
  is keyed by bank (not account).  Uses ``idx_txn_account_posted`` (O(log n)).
- ``effective_since(account_id)``: returns ``cursor - 3 days`` for use in
  window queries (covers pending-clearing movements that change date between
  runs).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from open_banca_domain.entities.transaction import Transaction
from open_banca_storage.dedup.fingerprint import compute_fingerprint

logger = logging.getLogger(__name__)

# Buffer for incremental window queries (Story 2 AC).
CURSOR_BUFFER_DAYS: int = 3

# Fuzzy transfer window for cross-account matching.
_TRANSFER_WINDOW_DAYS: int = 3


@dataclass
class IngestResult:
    """Result of a single ``DedupEngine.ingest()`` call.

    Attributes:
        new: Transactions that were inserted for the first time.
        duplicates: Transactions that were skipped (already in storage).
        transfer_pairs: Pairs of (debit_tx_id, credit_tx_id) that were linked.
            Note: these are the *original* Transaction objects passed to
            ``ingest()``.  The ``transfer_match_id`` field on each object
            will still be None because Pydantic models are frozen; the DB
            rows have been updated in place.  Callers that need the updated
            state should reload from the repository.
    """

    new: list[Transaction] = field(default_factory=list)
    duplicates: list[Transaction] = field(default_factory=list)
    transfer_pairs: list[tuple[Transaction, Transaction]] = field(default_factory=list)


class DedupEngine:
    """3-level deduplication engine backed by a SQLite/SQLCipher connection.

    Args:
        conn: An open DBAPI2 connection with the schema already migrated.
              The caller owns the connection lifecycle.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # ── Public API ────────────────────────────────────────────────────────────

    def ingest(self, transactions: list[Transaction]) -> IngestResult:
        """Process *transactions* through the 3-level dedup pipeline.

        1. Level 1 (embedded_id): skip if bank-supplied ID already indexed.
        2. Level 2 (fingerprint): skip if deterministic hash already indexed.
        3. Level 3 (transfer): for each new transaction, query the DB for an
           unlinked counterpart in another account (opposite sign, same amount,
           date within ±3 days).  This covers both same-batch and cross-batch
           transfer detection.

        Returns:
            ``IngestResult`` with new, duplicates, and transfer_pairs.
        """
        result = IngestResult()

        for tx in transactions:
            # ── Level 1: embedded ID ──────────────────────────────────────
            if tx.embedded_id is not None:
                if self._embedded_id_exists(tx.embedded_id, tx.account_id):
                    logger.debug(
                        "L1 skip: embedded_id=%s account=%s", tx.embedded_id, tx.account_id
                    )
                    result.duplicates.append(tx)
                    continue
                # New embedded-ID transaction — insert and index.
                self._insert_transaction(tx)
                self._index_embedded(tx)
                result.new.append(tx)
                continue

            # ── Level 2: fingerprint ──────────────────────────────────────
            fp = compute_fingerprint(
                account_id=tx.account_id,
                posted_at=tx.posted_at,
                value_at=tx.value_at,
                amount=tx.amount,
                description=tx.description,
            )
            if self._fingerprint_exists(fp, tx.account_id):
                logger.debug("L2 skip: fingerprint=%s account=%s", fp[:8], tx.account_id)
                result.duplicates.append(tx)
                continue
            self._insert_transaction(tx)
            self._index_fingerprint(fp, tx)
            result.new.append(tx)

        # ── Level 3: fuzzy transfer matching ─────────────────────────────
        # For each newly inserted transaction, query the DB for an unlinked
        # counterpart row in a *different* account that matches on amount and
        # date window.  This handles both same-batch and cross-batch cases.
        for tx in result.new:
            if tx.amount == Decimal("0"):
                continue
            counterpart_id = self._find_transfer_counterpart(tx)
            if counterpart_id is not None:
                self._link_transfer_pair(tx.id, counterpart_id)
                # Retrieve counterpart transaction for the result tuple.
                counterpart = self._load_tx_by_id(counterpart_id)
                if counterpart is not None:
                    # Debit first, credit second.
                    if tx.amount < Decimal("0"):
                        result.transfer_pairs.append((tx, counterpart))
                    else:
                        result.transfer_pairs.append((counterpart, tx))
                    logger.debug("L3 linked: %s <-> %s", tx.id, counterpart_id)

        return result

    def get_cursor(self, account_id: str) -> datetime | None:
        """Return the latest ``posted_at`` for *account_id*, or None.

        Derived directly from ``MAX(posted_at)`` in the ``transactions`` table
        so it always reflects persisted data.  Uses the
        ``idx_txn_account_posted`` composite index (O(log n)).

        Note: the existing ``cursors`` table is keyed by bank, not account.
        This method deliberately does not write to that table — it derives
        the per-account cursor from the transactions themselves.
        """
        row = self._conn.execute(
            "SELECT MAX(posted_at) FROM transactions WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return datetime.fromisoformat(row[0])

    def effective_since(self, account_id: str) -> datetime | None:
        """Return ``get_cursor() - 3 days`` for the incremental query window.

        The 3-day buffer ensures pending-clearing movements that may change
        date or amount between runs are re-processed through dedup (and
        upserted if they differ).

        Returns None if no cursor exists (first run - full historical import).
        """
        cursor = self.get_cursor(account_id)
        if cursor is None:
            return None
        return cursor - timedelta(days=CURSOR_BUFFER_DAYS)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _embedded_id_exists(self, embedded_id: str, account_id: str) -> bool:
        """Return True if a dedup_index row with level='embedded' exists."""
        row = self._conn.execute(
            "SELECT 1 FROM dedup_index WHERE fingerprint = ? AND account_id = ? AND level = 'embedded'",
            (embedded_id, account_id),
        ).fetchone()
        return row is not None

    def _fingerprint_exists(self, fingerprint: str, account_id: str) -> bool:
        """Return True if a dedup_index row with level='fingerprint' exists."""
        row = self._conn.execute(
            "SELECT 1 FROM dedup_index WHERE fingerprint = ? AND account_id = ? AND level = 'fingerprint'",
            (fingerprint, account_id),
        ).fetchone()
        return row is not None

    def _find_transfer_counterpart(self, tx: Transaction) -> str | None:
        """Query for an unlinked counterpart row for *tx* in another account.

        Criteria:
        - Different account.
        - Opposite sign (debit matched with credit).
        - Same absolute amount.
        - ``transfer_match_id IS NULL`` (not already linked).
        - Posting date within ±TRANSFER_WINDOW_DAYS.

        Returns the UUID of the first matching transaction, or None.
        """
        abs_amount = str(abs(tx.amount))
        # CAST(amount AS REAL) comparison avoids TEXT collation issues.
        # The sign filter: if tx.amount < 0 (debit), counterpart amount > 0.
        if tx.amount < Decimal("0"):
            sign_filter = "CAST(amount AS REAL) > 0"
        else:
            sign_filter = "CAST(amount AS REAL) < 0"

        window_days = _TRANSFER_WINDOW_DAYS
        row = self._conn.execute(
            f"""
            SELECT id FROM transactions
            WHERE account_id != ?
              AND ABS(CAST(amount AS REAL)) = CAST(? AS REAL)
              AND {sign_filter}
              AND transfer_match_id IS NULL
              AND ABS(
                  CAST(strftime('%s', posted_at) AS INTEGER) -
                  CAST(strftime('%s', ?) AS INTEGER)
              ) <= ?
            ORDER BY posted_at ASC
            LIMIT 1
            """,
            (
                tx.account_id,
                abs_amount,
                tx.posted_at.isoformat(),
                window_days * 86400,
            ),
        ).fetchone()
        return row[0] if row else None

    def _load_tx_by_id(self, tx_id: str) -> Transaction | None:
        """Load a minimal Transaction from the DB by UUID."""
        row = self._conn.execute(
            """
            SELECT id, account_id, posted_at, value_at, amount, currency,
                   description, fingerprint_hash, embedded_id, transfer_match_id
            FROM transactions WHERE id = ?
            """,
            (tx_id,),
        ).fetchone()
        if row is None:
            return None
        return Transaction(
            id=row[0],
            account_id=row[1],
            posted_at=datetime.fromisoformat(row[2]),
            value_at=datetime.fromisoformat(row[3]),
            amount=Decimal(row[4]),
            currency=row[5],
            description=row[6],
            fingerprint_hash=row[7],
            embedded_id=row[8],
            transfer_match_id=row[9],
        )

    def _insert_transaction(self, tx: Transaction) -> None:
        """Insert a transaction row (INSERT OR IGNORE - idempotent on UUID)."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO transactions (
                id, account_id, job_id, posted_at, value_at,
                amount, currency, description, fingerprint_hash,
                embedded_id, transfer_match_id, created_at
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tx.id,
                tx.account_id,
                tx.posted_at.isoformat(),
                tx.value_at.isoformat(),
                str(tx.amount),
                tx.currency,
                tx.description,
                tx.fingerprint_hash,
                tx.embedded_id,
                tx.transfer_match_id,
                datetime.now(tz=UTC).isoformat(),
            ),
        )
        self._conn.commit()

    def _index_embedded(self, tx: Transaction) -> None:
        """Add a dedup_index row for an embedded-ID transaction."""
        assert tx.embedded_id is not None
        self._conn.execute(
            """
            INSERT OR REPLACE INTO dedup_index (fingerprint, account_id, transaction_id, level)
            VALUES (?, ?, ?, 'embedded')
            """,
            (tx.embedded_id, tx.account_id, tx.id),
        )
        self._conn.commit()

    def _index_fingerprint(self, fingerprint: str, tx: Transaction) -> None:
        """Add a dedup_index row for a fingerprint-hashed transaction."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO dedup_index (fingerprint, account_id, transaction_id, level)
            VALUES (?, ?, ?, 'fingerprint')
            """,
            (fingerprint, tx.account_id, tx.id),
        )
        self._conn.commit()

    def _link_transfer_pair(self, debit_id: str, credit_id: str) -> None:
        """Set transfer_match_id on both sides."""
        self._conn.execute(
            "UPDATE transactions SET transfer_match_id = ? WHERE id = ?",
            (credit_id, debit_id),
        )
        self._conn.execute(
            "UPDATE transactions SET transfer_match_id = ? WHERE id = ?",
            (debit_id, credit_id),
        )
        self._conn.commit()
